#include "bridge/ownership.h"

namespace bridge {

std::string to_string(Side side) {
    return side == Side::sender ? "sender" : "receiver";
}

std::optional<Side> side_from_string(const std::string& value) {
    if (value == "sender") return Side::sender;
    if (value == "receiver") return Side::receiver;
    return std::nullopt;
}

std::string managed_name(const std::string& ns, Side side, const std::string& nmos_id,
                         const std::string& label) {
    std::string name = "NMOS(" + ns + ")/" + to_string(side) + "/" + nmos_id;
    if (!label.empty()) {
        name += " " + label;
    }
    return name;
}

std::optional<Ownership> parse_managed_name(const std::string& name) {
    std::string ns;
    std::string rest;

    static const std::string current_prefix = "NMOS(";
    if (name.rfind(current_prefix, 0) == 0) {
        const auto ns_end = name.find(")/", current_prefix.size());
        if (ns_end == std::string::npos) {
            return std::nullopt;
        }
        ns = name.substr(current_prefix.size(), ns_end - current_prefix.size());
        rest = name.substr(ns_end + 2);
    } else {
        static const std::string legacy_prefix = "NMOS[";
        if (name.rfind(legacy_prefix, 0) == 0) {
            const auto ns_end = name.find("]/", legacy_prefix.size());
            if (ns_end == std::string::npos) {
                return std::nullopt;
            }
            ns = name.substr(legacy_prefix.size(), ns_end - legacy_prefix.size());
            rest = name.substr(ns_end + 2);
        } else {
            static const std::string stripped_prefix = "NMOS";
            if (name.rfind(stripped_prefix, 0) != 0) {
                return std::nullopt;
            }
            const auto ns_end = name.find('/', stripped_prefix.size());
            if (ns_end == std::string::npos) {
                return std::nullopt;
            }
            ns = name.substr(stripped_prefix.size(), ns_end - stripped_prefix.size());
            rest = name.substr(ns_end + 1);
        }
    }

    const auto side_end = rest.find('/');
    if (side_end == std::string::npos) {
        return std::nullopt;
    }
    const std::string side_str = rest.substr(0, side_end);
    const auto side = side_from_string(side_str);
    if (!side.has_value()) {
        return std::nullopt;
    }

    // nmos_id runs from after the side slash to the first space (label separator).
    std::string after = rest.substr(side_end + 1);
    const auto space = after.find(' ');
    const std::string nmos_id = (space == std::string::npos) ? after : after.substr(0, space);

    if (ns.empty() || nmos_id.empty()) {
        return std::nullopt;
    }

    return Ownership{ns, *side, nmos_id};
}

}  // namespace bridge
