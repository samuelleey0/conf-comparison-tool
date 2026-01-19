import yaml
import os
import uuid
from typing import List, Dict, Optional


class RubricManager:
    def __init__(self, storage_path: str = "./rubrics"):
        self.storage_path = storage_path

        # Ensure the storage directory exists
        if not os.path.exists(self.storage_path):
            os.makedirs(self.storage_path)

    def get_path(self, rubric_id: str) -> str:
        return os.path.join(self.storage_path, f"{rubric_id}.yaml")

    def create_rubric(self, data: Dict) -> str:
        """
        Save a new rubric to a YAML file and return its unique ID.
        """

        # Generate a unique ID for the rubric
        rubric_id = data.get("id") or str(uuid.uuid4())[:8]
        data["id"] = rubric_id

        file_path = self.get_path(rubric_id)

        with open(file_path, "w") as file:
            yaml.dump(data, file, sort_keys=False)
        path = self.get_path(rubric_id)

        return rubric_id

    def get_rubrics(self) -> List[Dict]:
        """
        Return a list of all saved rubrics.
        """

        rubrics = []
        for filename in os.listdir(self.storage_path):
            if filename.endswith(".yaml"):
                with open(os.path.join(self.storage_path, filename), "r") as f:
                    rubric = yaml.safe_load(f)

                    # Return only essential info for the list view
                    rubrics.append(
                        {
                            "id": rubric.get("id"),
                            "name": rubric.get("name"),
                            "description": rubric.get("description", ""),
                        }
                    )
        return rubrics

    def get_rubric_detail(self, rubric_id: str) -> Optional[Dict]:
        """
        Return the full content of a specific rubric.
        """

        file_path = self.get_path(rubric_id)
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                return yaml.safe_load(f)
        return None

    def delete_rubric(self, rubric_id: str) -> bool:
        """
        Delete a rubric file by its ID.
        """
        file_path = self.get_path(rubric_id)
        if os.path.exists(file_path):
            os.remove(file_path)
            return True
        return False

    def update_rubric(self, rubric_id: str, data: Dict) -> bool:
        """
        Overwrite an existing rubric with new data. Return True on success.
        """

        file_path = self.get_path(rubric_id)
        if not os.path.exists(file_path):
            return False  # Rubric does not exist

        data["id"] = rubric_id  # Ensure the ID remains the same
        with open(file_path, "w") as f:
            yaml.dump(data, f, sort_keys=False)
        return True

    def validate_rubric(self, data: Dict) -> bool:
        """
        Validate the structure of a rubric.
        Returns True if valid, False otherwise.
        """

        if not isinstance(data, dict):
            return False

        if "name" not in data:
            return False

        secs = data.get("sections", [])
        if not isinstance(secs, list) or not secs:
            return False
        for sec in secs:
            if not isinstance(sec, dict) or "name" not in sec:
                return False
        return True

    def create_rubric_interactive(self) -> str:
        """
        Interactive prompt to create a new rubric.
        Returns the rubric ID.
        """
        name = input("Enter rubric name: ").strip()
        description = input("Enter rubric description (optional): ").strip()
        sections = []
        print("Add sections. Empty name to finish.")
        while True:
            sec_name = input(" Enter section name: ").strip()
            if not sec_name:
                break
            sec_type = (
                input(" type (regex/contains/block_diff/diff_lines) [contains]: ")
                .strip()
                .lower()
                or "contains"
            )
            pattern = ""
            start = ""
            if sec_type in ("regex", "contains"):
                pattern = input(" pattern to search for: ").strip()
            elif sec_type == "block_diff":
                start = input(" block start prefix (e.g. 'interface): ").strip()
            pts = float(input(" points for this section: ").strip() or "0")
            section = {"name": sec_name, "type": sec_type, "points": pts}
            if pattern:
                section["pattern"] = pattern
            if start:
                section["start"] = start
            sections.append(section)

        payload = {"name": name, "description": description, "sections": sections}
        rubric_id = self.create_rubric(payload)
        print(f"[INFO] Rubric '{name}' created with ID: {rubric_id}")
        return rubric_id
