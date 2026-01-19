import yaml
import os
import uuid
import json
from typing import List, Dict


class SchemeManager:
    def __init__(self, storage_path: str = "./schemes"):
        self.storage_path = storage_path

        # Ensure the storage directory exists
        if not os.path.exists(self.storage_path):
            os.makedirs(self.storage_path)

    def save_scheme(self, data: Dict) -> str:
        """
        Save a new scheme to a YAML file and return its unique ID.
        """

        # Generate a unique ID for the scheme
        scheme_id = data.get("id") or str(uuid.uuid4())[:8]
        data["id"] = scheme_id

        file_path = os.path.join(self.storage_path, f"{scheme_id}.yaml")
        with open(file_path, "w") as f:
            yaml.dump(data, f, sort_keys=False)
        return scheme_id

    def get_all_schemes(self) -> List[Dict]:
        schemes = []
        if not os.path.exists(self.storage_path):
            return []
        for filename in os.listdir(self.storage_path):
            if filename.endswith(".yaml"):
                with open(os.path.join(self.storage_path, filename), "r") as f:
                    scheme = yaml.safe_load(f)
                    schemes.append(scheme)
        return schemes

    def get_scheme_by_id(self, scheme_id: str) -> Dict:
        file_path = os.path.join(self.storage_path, f"{scheme_id}.yaml")
        with open(file_path, "r") as f:
            scheme = yaml.safe_load(f)
        return scheme

    def update_scheme(self, scheme_id: str, data: Dict) -> bool:
        """
        Overwrite an existing scheme with new data. Return True on success.
        """

        file_path = os.path.join(self.storage_path, f"{scheme_id}.yaml")
        if not os.path.exists(file_path):
            return False  # Scheme does not exist

        data["id"] = scheme_id  # Ensure the ID remains the same
        with open(file_path, "w") as f:
            yaml.dump(data, f, sort_keys=False)
        return True

    def delete_scheme(self, scheme_id: str) -> bool:
        """
        Delete a scheme file by its ID. Return True on success.
        """
        file_path = os.path.join(self.storage_path, f"{scheme_id}.yaml")
        if not os.path.exists(file_path):
            return False
        try:
            os.remove(file_path)
            return True
        except Exception:
            return False

    def validate_scheme(self, data: Dict) -> bool:
        """
        Basic validation to ensure required fields are present.
        """

        if not isinstance(data, dict):
            return False
        vars_ = data.get("variables", {})
        return isinstance(vars_, dict)

    def create_scheme_interactive(self) -> str:
        """
        Interactive prompt to create a new scheme.
        Returns the scheme ID.
        """
        name = input("Enter scheme name: ").strip()
        vars_raw = {}
        print(
            "Define variables (key=value) for this scheme (enter 'done' when finished):"
        )
        while True:
            line = input("> ").strip()
            if not line:
                break
            if "=" not in line:
                print("Invalid format. Use key=value.")
                continue
            key, value = line.split("=", 1)
            vars_raw[key.strip()] = value.strip()
        payload = {"name": name, "variables": vars_raw}
        scheme_id = self.save_scheme(payload)
        print(f"[INFO] Scheme '{name}' created with ID: {scheme_id}")
        return scheme_id

    def prepare_rubric_for_grading(self, rubric: Dict, scheme: Dict) -> Dict:
        """
        Merges scheme variables into the rubric.
        Handles both simple strings and complex objects (lists).
        """
        # We work on a copy to avoid changing the original template
        final_rubric = json.loads(json.dumps(rubric))
        variables = scheme.get("variables", {})

        for crit in final_rubric.get("criteria", []):
            pattern = crit.get("pattern", "")

            # 1. Handle List Variables (e.g., VLANs)
            # If the pattern contains a reference like {{vlans}},
            # we attach the raw list to the criterion for the grader to use.
            if "{{" in pattern:
                for var_name, var_value in variables.items():
                    placeholder = "{{" + var_name + "}}"
                    if placeholder in pattern:
                        if isinstance(var_value, list):
                            # Attach the actual list data to the criterion
                            crit["dynamic_data"] = var_value
                        else:
                            # Standard string replacement for Hostname/IP
                            crit["pattern"] = pattern.replace(
                                placeholder, str(var_value)
                            )

        return final_rubric
