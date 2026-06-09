#pragma once

#include <optional>
#include <string>

namespace bridge {

enum class Side { sender, receiver };

std::string to_string(Side side);
std::optional<Side> side_from_string(const std::string& value);

// Ownership encoded in the AES67 daemon stream name, since the daemon exposes no
// custom per-stream metadata field:
//
//   NMOS(<namespace>)/sender/<nmos_id> <label>
//   NMOS(<namespace>)/receiver/<nmos_id> <label>
struct Ownership {
    std::string ns;
    Side side;
    std::string nmos_id;
};

std::string managed_name(const std::string& ns, Side side, const std::string& nmos_id,
                         const std::string& label);

// Returns the parsed ownership, or std::nullopt if the name is not a well-formed
// bridge-managed name (e.g. a manually-created stream).
std::optional<Ownership> parse_managed_name(const std::string& name);

}  // namespace bridge
