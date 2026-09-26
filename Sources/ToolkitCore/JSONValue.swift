import Foundation

/// A `Sendable` JSON value for tool output whose schema is versioned by someone else
/// (CoreDevice, simctl). Accessors are optional so an unexpected shape never crashes.
public enum JSONValue: Sendable, Hashable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    public init(foundation object: Any) {
        switch object {
        case is NSNull:
            self = .null
        case let value as NSNumber:
            if CFGetTypeID(value) == CFBooleanGetTypeID() {
                self = .bool(value.boolValue)
            } else {
                self = .number(value.doubleValue)
            }
        case let value as String:
            self = .string(value)
        case let value as [Any]:
            self = .array(value.map(JSONValue.init(foundation:)))
        case let value as [String: Any]:
            self = .object(value.mapValues(JSONValue.init(foundation:)))
        default:
            self = .string(String(describing: object))
        }
    }

    public static func parse(_ data: Data) throws -> JSONValue {
        do {
            let object = try JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed])
            return JSONValue(foundation: object)
        } catch {
            throw ToolkitError(.protocolViolation, message: "A developer tool returned output that is not valid JSON.", technicalDetail: String(describing: error))
        }
    }

    public subscript(key: String) -> JSONValue? {
        if case .object(let object) = self { return object[key] }
        return nil
    }

    public subscript(index: Int) -> JSONValue? {
        if case .array(let array) = self, array.indices.contains(index) { return array[index] }
        return nil
    }

    /// Follows a dotted path such as "result.devices".
    public func value(at path: String) -> JSONValue? {
        var current: JSONValue? = self
        for component in path.split(separator: ".") {
            current = current?[String(component)]
        }
        return current
    }

    public var string: String? {
        switch self {
        case .string(let value): return value
        case .number(let value):
            if value.rounded() == value, abs(value) < 1e15 { return String(Int64(value)) }
            return String(value)
        case .bool(let value): return value ? "true" : "false"
        default: return nil
        }
    }

    public var nonEmptyString: String? {
        guard let value = string?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else { return nil }
        return value
    }

    public var bool: Bool? {
        switch self {
        case .bool(let value): return value
        case .string(let value):
            switch value.lowercased() {
            case "true", "yes", "enabled": return true
            case "false", "no", "disabled": return false
            default: return nil
            }
        default: return nil
        }
    }

    public var double: Double? {
        switch self {
        case .number(let value): return value
        case .string(let value): return Double(value)
        default: return nil
        }
    }

    public var int: Int? {
        guard let value = double, value.isFinite, value.rounded() == value, abs(value) < 9e15 else { return nil }
        return Int(value)
    }

    public var array: [JSONValue]? {
        if case .array(let value) = self { return value }
        return nil
    }

    public var object: [String: JSONValue]? {
        if case .object(let value) = self { return value }
        return nil
    }

    public var foundationObject: Any {
        switch self {
        case .null: return NSNull()
        case .bool(let value): return NSNumber(value: value)
        case .number(let value): return NSNumber(value: value)
        case .string(let value): return value
        case .array(let value): return value.map(\.foundationObject)
        case .object(let value): return value.mapValues(\.foundationObject)
        }
    }

    public func prettyString() -> String {
        guard let data = try? JSONSerialization.data(withJSONObject: foundationObject, options: [.prettyPrinted, .sortedKeys, .fragmentsAllowed, .withoutEscapingSlashes]) else {
            return String(describing: self)
        }
        return String(decoding: data, as: UTF8.self)
    }
}
