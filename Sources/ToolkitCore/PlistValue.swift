import Foundation

/// A `Sendable`, strongly typed property-list value.
///
/// Device protocols exchange property lists whose shape is controlled by the device, so they are
/// treated as untrusted input: every accessor is optional and nothing is force-cast.
public enum PlistValue: Sendable, Hashable {
    case string(String)
    case integer(Int64)
    case unsignedInteger(UInt64)
    case real(Double)
    case boolean(Bool)
    case date(Date)
    case data(Data)
    case array([PlistValue])
    case dictionary([String: PlistValue])

    // MARK: Accessors

    public var stringValue: String? {
        if case .string(let value) = self { return value }
        return nil
    }

    public var intValue: Int? {
        switch self {
        case .integer(let value): return Int(exactly: value)
        case .unsignedInteger(let value): return Int(exactly: value)
        case .real(let value):
            guard value.isFinite, value.rounded() == value else { return nil }
            return Int(exactly: value)
        default: return nil
        }
    }

    public var int64Value: Int64? {
        switch self {
        case .integer(let value): return value
        case .unsignedInteger(let value): return Int64(exactly: value)
        default: return nil
        }
    }

    public var uint64Value: UInt64? {
        switch self {
        case .integer(let value): return UInt64(exactly: value)
        case .unsignedInteger(let value): return value
        default: return nil
        }
    }

    public var doubleValue: Double? {
        switch self {
        case .real(let value): return value
        case .integer(let value): return Double(value)
        case .unsignedInteger(let value): return Double(value)
        default: return nil
        }
    }

    public var boolValue: Bool? {
        if case .boolean(let value) = self { return value }
        return nil
    }

    public var dateValue: Date? {
        if case .date(let value) = self { return value }
        return nil
    }

    public var dataValue: Data? {
        if case .data(let value) = self { return value }
        return nil
    }

    public var arrayValue: [PlistValue]? {
        if case .array(let value) = self { return value }
        return nil
    }

    public var dictionaryValue: [String: PlistValue]? {
        if case .dictionary(let value) = self { return value }
        return nil
    }

    public subscript(key: String) -> PlistValue? {
        dictionaryValue?[key]
    }

    public subscript(index: Int) -> PlistValue? {
        guard let array = arrayValue, array.indices.contains(index) else { return nil }
        return array[index]
    }

    // MARK: Conversion from Foundation

    public init?(foundation object: Any) {
        switch object {
        case let value as String:
            self = .string(value)
        case let value as NSNumber:
            if CFGetTypeID(value) == CFBooleanGetTypeID() {
                self = .boolean(value.boolValue)
            } else if CFNumberIsFloatType(value) {
                self = .real(value.doubleValue)
            } else if value.int64Value < 0 {
                self = .integer(value.int64Value)
            } else if value.uint64Value > UInt64(Int64.max) {
                self = .unsignedInteger(value.uint64Value)
            } else {
                self = .integer(value.int64Value)
            }
        case let value as Date:
            self = .date(value)
        case let value as Data:
            self = .data(value)
        case let value as [Any]:
            var items: [PlistValue] = []
            items.reserveCapacity(value.count)
            for item in value {
                guard let converted = PlistValue(foundation: item) else { return nil }
                items.append(converted)
            }
            self = .array(items)
        case let value as [String: Any]:
            var items: [String: PlistValue] = [:]
            for (key, item) in value {
                guard let converted = PlistValue(foundation: item) else { return nil }
                items[key] = converted
            }
            self = .dictionary(items)
        default:
            // Keyed-archiver UIDs and other exotic CF types are preserved as a description so
            // an unexpected value never crashes parsing.
            self = .string(String(describing: object))
        }
    }

    public var foundationObject: Any {
        switch self {
        case .string(let value): return value
        case .integer(let value): return NSNumber(value: value)
        case .unsignedInteger(let value): return NSNumber(value: value)
        case .real(let value): return NSNumber(value: value)
        case .boolean(let value): return NSNumber(value: value)
        case .date(let value): return value
        case .data(let value): return value
        case .array(let value): return value.map(\.foundationObject)
        case .dictionary(let value): return value.mapValues(\.foundationObject)
        }
    }

    // MARK: Serialization

    public static func decode(_ data: Data) throws -> PlistValue {
        let object: Any
        do {
            object = try PropertyListSerialization.propertyList(from: data, options: [], format: nil)
        } catch {
            throw ToolkitError(
                .protocolViolation,
                message: "The device returned data that is not a valid property list.",
                technicalDetail: String(describing: error)
            )
        }
        guard let value = PlistValue(foundation: object) else {
            throw ToolkitError(.protocolViolation, message: "The device returned an unsupported property-list value.")
        }
        return value
    }

    public func encoded(format: PropertyListSerialization.PropertyListFormat = .xml) throws -> Data {
        do {
            return try PropertyListSerialization.data(fromPropertyList: foundationObject, format: format, options: 0)
        } catch {
            throw ToolkitError(.internalInconsistency, message: "Could not encode a property list.", technicalDetail: String(describing: error))
        }
    }

    /// A readable, deterministic rendering for "raw details" views and snapshot files.
    public func prettyJSONString() -> String {
        let object = jsonCompatibleObject
        guard JSONSerialization.isValidJSONObject(object),
              let data = try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]),
              let text = String(data: data, encoding: .utf8)
        else {
            return String(describing: self)
        }
        return text
    }

    public var jsonCompatibleObject: Any {
        switch self {
        case .string(let value): return value
        case .integer(let value): return NSNumber(value: value)
        case .unsignedInteger(let value): return NSNumber(value: value)
        case .real(let value): return value.isFinite ? NSNumber(value: value) : String(value)
        case .boolean(let value): return NSNumber(value: value)
        case .date(let value): return ISO8601DateFormatter().string(from: value)
        case .data(let value):
            if value.count <= 64 {
                return "<data \(value.count) bytes: \(value.base64EncodedString())>"
            }
            return "<data \(value.count) bytes>"
        case .array(let value): return value.map(\.jsonCompatibleObject)
        case .dictionary(let value): return value.mapValues(\.jsonCompatibleObject)
        }
    }
}

extension PlistValue: ExpressibleByStringLiteral, ExpressibleByIntegerLiteral, ExpressibleByBooleanLiteral,
    ExpressibleByArrayLiteral, ExpressibleByDictionaryLiteral, ExpressibleByFloatLiteral
{
    public init(stringLiteral value: String) { self = .string(value) }
    public init(integerLiteral value: Int64) { self = .integer(value) }
    public init(booleanLiteral value: Bool) { self = .boolean(value) }
    public init(floatLiteral value: Double) { self = .real(value) }
    public init(arrayLiteral elements: PlistValue...) { self = .array(elements) }
    public init(dictionaryLiteral elements: (String, PlistValue)...) {
        var dictionary: [String: PlistValue] = [:]
        for (key, value) in elements { dictionary[key] = value }
        self = .dictionary(dictionary)
    }
}
