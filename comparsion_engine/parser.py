from compare_utils import should_ignore


def parse_showrun(file_path):
    """
    Parses show running-config into a nested dictionary.
    Sections: hostname, interfaces, banner, vty, users
    """
    config = {
        "hostname": None,
        "interfaces": {},
        "banner_motd": None,
        "vty": {},
        "users": [],
    }

    current_interface = None
    with open(file_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or should_ignore(line):
                continue

            # Hostname
            if line.startswith("hostname"):
                config["hostname"] = line.split()[1]

            # Interface section
            elif line.startswith("interface"):
                current_interface = line.split()[1]
                config["interfaces"][current_interface] = {}
            elif current_interface:
                if line.startswith("ip address"):
                    parts = line.split()
                    config["interfaces"][current_interface]["ip"] = parts[2]
                    config["interfaces"][current_interface]["mask"] = parts[3]
                elif line == "shutdown":
                    config["interfaces"][current_interface]["shutdown"] = True
                elif line == "no shutdown":
                    config["interfaces"][current_interface]["shutdown"] = False
                elif line.startswith("description"):
                    config["interfaces"][current_interface]["description"] = " ".join(
                        line.split()[1:]
                    )
                elif line.startswith("!"):  # exit interface
                    current_interface = None

            # Banner MOTD
            elif line.startswith("banner motd"):
                try:
                    config["banner_motd"] = line.split("^C")[1]
                except:
                    config["banner_motd"] = "configured"

            # Line vty
            elif line.startswith("line vty"):
                config["vty"]["line"] = line
            elif line.startswith("login"):
                config["vty"]["login"] = line
            elif line.startswith("transport input"):
                config["vty"]["transport"] = line

            # Users
            elif line.startswith("username"):
                parts = line.split()
                if "secret" in parts:
                    privilege = parts[3] if "privilege" in parts else "1"
                    config["users"].append(
                        {"username": parts[1], "privilege": privilege}
                    )

    return config
