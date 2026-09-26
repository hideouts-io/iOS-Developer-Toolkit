import Foundation
import NIOCore
import NIOPosix
import NIOSSL
import NIOTLS
import Security
import ToolkitCore

/// Credentials used to upgrade a lockdown or service connection to TLS.
public struct TLSCredentials: Sendable {
    public let certificatePEM: Data
    public let privateKeyPEM: Data
    /// The device certificate from the pair record. The server certificate presented during
    /// the handshake must match it (same certificate or same public key).
    public let pinnedDeviceCertificatePEM: Data?

    public init(certificatePEM: Data, privateKeyPEM: Data, pinnedDeviceCertificatePEM: Data?) {
        self.certificatePEM = certificatePEM
        self.privateKeyPEM = privateKeyPEM
        self.pinnedDeviceCertificatePEM = pinnedDeviceCertificatePEM
    }
}

/// A bidirectional byte stream to usbmuxd, a device port, or a test server, with
/// `read(exactly:)` semantics and an in-place TLS upgrade.
public final class DeviceChannel: Sendable {
    let channel: Channel
    let inbound: InboundBuffer
    public let description: String

    init(channel: Channel, inbound: InboundBuffer, description: String) {
        self.channel = channel
        self.inbound = inbound
        self.description = description
    }

    /// Connects to a Unix-domain socket (usbmuxd).
    public static func connect(unixSocketPath path: String, description: String, timeout: TimeInterval = 10) async throws -> DeviceChannel {
        let inbound = InboundBuffer()
        let bootstrap = ClientBootstrap(group: MultiThreadedEventLoopGroup.singleton)
            .connectTimeout(.milliseconds(Int64(timeout * 1000)))
            .channelInitializer { channel in
                channel.eventLoop.makeCompletedFuture {
                    try channel.pipeline.syncOperations.addHandler(inbound)
                }
            }
        do {
            let channel = try await bootstrap.connect(unixDomainSocketPath: path).get()
            return DeviceChannel(channel: channel, inbound: inbound, description: description)
        } catch {
            throw ToolkitError(
                .serviceUnavailable,
                message: "The macOS device service (usbmuxd) is not reachable.",
                recovery: "Reconnect the device. If the problem continues, restart the Mac; the toolkit never restarts system services itself.",
                technicalDetail: "\(path): \(error)"
            )
        }
    }

    public var isActive: Bool { channel.isActive }

    public func write(_ data: Data) async throws {
        var buffer = channel.allocator.buffer(capacity: data.count)
        buffer.writeBytes(data)
        do {
            try await channel.writeAndFlush(buffer).get()
        } catch {
            throw ToolkitError.deviceCommunication(technicalDetail: "Write to \(description) failed: \(error)")
        }
    }

    /// Reads exactly `count` bytes or throws if the connection closes first.
    public func read(exactly count: Int, timeout: TimeInterval? = 30) async throws -> Data {
        guard count > 0 else { return Data() }
        if let timeout {
            let inbound = self.inbound
            let description = self.description
            return try await withTimeout(timeout, operation: "Reading from \(description)") {
                try await inbound.read(exactly: count, source: description)
            }
        }
        return try await inbound.read(exactly: count, source: description)
    }

    /// Reads whatever is available (at least one byte), or returns nil at end of stream.
    public func readSome(maximum: Int = 64 * 1024) async throws -> Data? {
        try await inbound.readSome(maximum: maximum, source: description)
    }

    /// Waits until at least one byte is available (true) or the peer has closed cleanly (false).
    /// Streaming services use this so a close at a record boundary ends the stream normally.
    public func hasMoreData() async throws -> Bool {
        try await inbound.hasMoreData(source: description)
    }

    /// Upgrades the established stream to TLS using the pair record's host identity.
    public func startTLS(_ credentials: TLSCredentials, timeout: TimeInterval = 20) async throws {
        let context: NIOSSLContext
        do {
            let certificates = try NIOSSLCertificate.fromPEMBytes(Array(credentials.certificatePEM))
            let key = try NIOSSLPrivateKey(bytes: Array(credentials.privateKeyPEM), format: .pem)
            var configuration = TLSConfiguration.makeClientConfiguration()
            configuration.certificateChain = certificates.map { .certificate($0) }
            configuration.privateKey = .privateKey(key)
            configuration.certificateVerification = .noHostnameVerification
            configuration.minimumTLSVersion = .tlsv12
            context = try NIOSSLContext(configuration: configuration)
        } catch {
            throw ToolkitError(
                .notPaired,
                message: "The pairing record for this device could not be used.",
                recovery: "Disconnect and reconnect the device, unlock it, and trust this Mac again.",
                technicalDetail: "TLS configuration failed: \(error)"
            )
        }

        let pinned = credentials.pinnedDeviceCertificatePEM
        let inbound = self.inbound
        inbound.prepareForTLS()
        try await channel.eventLoop.submit { [channel] in
            let handler = try NIOSSLClientHandler(context: context, serverHostname: nil) { presented, promise in
                promise.succeed(CertificatePinning.verify(presented: presented, pinnedPEM: pinned) ? .certificateVerified : .failed)
            }
            try channel.pipeline.syncOperations.addHandler(handler, position: .first)
        }.get()

        let description = self.description
        try await withTimeout(timeout, operation: "Secure connection to \(description)") {
            try await inbound.waitForTLSHandshake(source: description)
        }
    }

    public func close() async {
        try? await channel.close().get()
    }
}

/// Compares the certificate the device presents with the one recorded at pairing time.
enum CertificatePinning {
    static func verify(presented: [NIOSSLCertificate], pinnedPEM: Data?) -> Bool {
        guard let pinnedPEM else { return true }
        guard let leaf = presented.first, let leafDER = try? Data(leaf.toDERBytes()) else { return false }
        guard let pinned = try? NIOSSLCertificate.fromPEMBytes(Array(pinnedPEM)).first,
              let pinnedDER = try? Data(pinned.toDERBytes())
        else { return false }
        if leafDER == pinnedDER { return true }
        // A re-issued certificate for the same device key is also acceptable.
        guard let leafKey = publicKey(fromDER: leafDER), let pinnedKey = publicKey(fromDER: pinnedDER) else { return false }
        return leafKey == pinnedKey
    }

    static func publicKey(fromDER der: Data) -> Data? {
        guard let certificate = SecCertificateCreateWithData(nil, der as CFData),
              let key = SecCertificateCopyKey(certificate),
              let external = SecKeyCopyExternalRepresentation(key, nil)
        else { return nil }
        return external as Data
    }
}

/// Buffers inbound bytes and wakes a single waiting reader.
final class InboundBuffer: ChannelInboundHandler, @unchecked Sendable {
    typealias InboundIn = ByteBuffer

    private enum Waiter {
        case bytes(Int, CheckedContinuation<Void, Error>)
        case anyBytes(CheckedContinuation<Void, Error>)
        case tls(CheckedContinuation<Void, Error>)
    }

    private let lock = NSLock()
    private var buffer = Data()
    private var closed = false
    private var failure: Error?
    private var waiter: Waiter?
    private var tlsCompleted = false
    /// Hard ceiling on buffered, unread bytes to bound memory if a peer floods the stream.
    private let maximumBufferedBytes = 256 * 1024 * 1024

    func channelRead(context: ChannelHandlerContext, data: NIOAny) {
        var bytes = unwrapInboundIn(data)
        guard let array = bytes.readBytes(length: bytes.readableBytes) else { return }
        lock.lock()
        buffer.append(contentsOf: array)
        let overflow = buffer.count > maximumBufferedBytes
        if overflow {
            failure = ToolkitError(.protocolViolation, message: "The device sent more data than the toolkit can buffer.")
        }
        let resume = takeSatisfiedWaiter()
        lock.unlock()
        resume?()
        if overflow { context.close(promise: nil) }
    }

    func channelInactive(context: ChannelHandlerContext) {
        lock.lock()
        closed = true
        let resume = takeSatisfiedWaiter()
        lock.unlock()
        resume?()
        context.fireChannelInactive()
    }

    func errorCaught(context: ChannelHandlerContext, error: Error) {
        lock.lock()
        if failure == nil { failure = error }
        let resume = takeSatisfiedWaiter()
        lock.unlock()
        resume?()
        context.close(promise: nil)
    }

    func userInboundEventTriggered(context: ChannelHandlerContext, event: Any) {
        if let tlsEvent = event as? TLSUserEvent, case .handshakeCompleted = tlsEvent {
            lock.lock()
            tlsCompleted = true
            let resume = takeSatisfiedWaiter()
            lock.unlock()
            resume?()
        }
        context.fireUserInboundEventTriggered(event)
    }

    func prepareForTLS() {
        lock.lock()
        tlsCompleted = false
        lock.unlock()
    }

    /// Must be called with the lock held. Returns a closure that resumes the waiter outside it.
    private func takeSatisfiedWaiter() -> (() -> Void)? {
        guard let waiter else { return nil }
        switch waiter {
        case .bytes(let count, let continuation):
            if buffer.count >= count {
                self.waiter = nil
                return { continuation.resume() }
            }
            if let failure {
                self.waiter = nil
                return { continuation.resume(throwing: failure) }
            }
            if closed {
                self.waiter = nil
                return { continuation.resume(throwing: InboundBuffer.closedError) }
            }
        case .anyBytes(let continuation):
            if !buffer.isEmpty || closed || failure != nil {
                self.waiter = nil
                return { continuation.resume() }
            }
        case .tls(let continuation):
            if tlsCompleted {
                self.waiter = nil
                return { continuation.resume() }
            }
            if let failure {
                self.waiter = nil
                return { continuation.resume(throwing: failure) }
            }
            if closed {
                self.waiter = nil
                return { continuation.resume(throwing: InboundBuffer.closedError) }
            }
        }
        return nil
    }

    private static let closedError = ToolkitError(
        .deviceDisconnected,
        message: "The connection to the device closed unexpectedly.",
        recovery: "Make sure the device is unlocked, connected, and has trusted this Mac."
    )

    private func install(_ makeWaiter: (CheckedContinuation<Void, Error>) -> Waiter) async throws {
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                lock.lock()
                if waiter != nil {
                    lock.unlock()
                    continuation.resume(throwing: ToolkitError(.internalInconsistency, message: "Two readers used one device connection."))
                    return
                }
                waiter = makeWaiter(continuation)
                let resume = takeSatisfiedWaiter()
                lock.unlock()
                resume?()
            }
        } onCancel: {
            lock.lock()
            let pending = waiter
            waiter = nil
            lock.unlock()
            switch pending {
            case .bytes(_, let continuation), .anyBytes(let continuation), .tls(let continuation):
                continuation.resume(throwing: CancellationError())
            case .none:
                break
            }
        }
    }

    func read(exactly count: Int, source: String) async throws -> Data {
        try await install { .bytes(count, $0) }
        return try lock.withLock {
            guard buffer.count >= count else {
                throw failure.map { ToolkitError.deviceCommunication(technicalDetail: "\(source): \($0)") } ?? InboundBuffer.closedError
            }
            let chunk = buffer.prefix(count)
            buffer.removeFirst(count)
            return Data(chunk)
        }
    }

    func readSome(maximum: Int, source: String) async throws -> Data? {
        try await install { .anyBytes($0) }
        return try lock.withLock {
            if !buffer.isEmpty {
                let count = min(maximum, buffer.count)
                let chunk = buffer.prefix(count)
                buffer.removeFirst(count)
                return Data(chunk)
            }
            if let failure {
                throw ToolkitError.deviceCommunication(technicalDetail: "\(source): \(failure)")
            }
            return nil
        }
    }

    func hasMoreData(source: String) async throws -> Bool {
        try await install { .anyBytes($0) }
        return try lock.withLock {
            if !buffer.isEmpty { return true }
            if let failure {
                throw ToolkitError.deviceCommunication(technicalDetail: "\(source): \(failure)")
            }
            return false
        }
    }

    func waitForTLSHandshake(source: String) async throws {
        do {
            try await install { .tls($0) }
        } catch let error as ToolkitError {
            throw error
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw ToolkitError(
                .notPaired,
                message: "A secure connection to the device could not be established.",
                recovery: "Unlock the device and make sure it still trusts this Mac. If you recently reset the device or its trust settings, disconnect it and trust this Mac again.",
                technicalDetail: "\(source): \(error)"
            )
        }
    }
}
