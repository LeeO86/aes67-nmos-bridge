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
    std::string name = "NMOS[" + ns + "]/" + to_string(side) + "/" + nmos_id;
    if (!label.empty()) {
        name += " " + label;
    }
    return name;
}

std::optional<Ownership> parse_managed_name(const std::string& name) {
    static const std::string prefix = "NMOS[";
    if (name.rfind(prefix, 0) != 0) {
        return std::nullopt;
    }

    const auto ns_end = name.find("]/", prefix.size());
    if (ns_end == std::string::npos) {
        return std::nullopt;
    }

    const std::string ns = name.substr(prefix.size(), ns_end - prefix.size());
    const std::string rest = name.substr(ns_end + 2);

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
