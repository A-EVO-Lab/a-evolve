"""
SkillLoader - Load and manage skills from SKILL.md files.

Implements learn-claude-code's progressive disclosure pattern:
- Layer 1: Metadata (name + description) - always loaded  
- Layer 2: Full SKILL.md body - loaded on-demand
- Layer 3: Resources (scripts/, references/, assets/) - loaded as needed

SKILL.md Format:
    ---
    name: skill_name
    description: Short description for skill selection
    ---

    # Skill Title

    ## When to Use
    ...

    ## Instructions
    ...
"""

import os
import re
from pathlib import Path
from typing import Dict, List, Any, Optional


class SkillLoader:
    """
    Loads and manages skills from SKILL.md files.
    
    A skill is a FOLDER containing:
    - SKILL.md (required): YAML frontmatter + markdown instructions
    - scripts/ (optional): Helper scripts the model can run
    - references/ (optional): Additional documentation
    - assets/ (optional): Templates, files for output
    
    This follows the learn-claude-code pattern for progressive disclosure.
    """
    
    def __init__(self, skills_dir: str, verbose: bool = False):
        """
        Initialize the skill loader.
        
        Args:
            skills_dir: Directory containing skill folders
            verbose: Enable verbose output
        """
        self.skills_dir = Path(skills_dir)
        self.verbose = verbose
        self.skills: Dict[str, Dict] = {}
        self.load_skills()
    
    def parse_skill_md(self, path: Path) -> Optional[Dict]:
        """
        Parse a SKILL.md file into metadata and body.
        
        Returns dict with: name, description, body, path, dir
        Returns None if file doesn't match format.
        """
        try:
            content = path.read_text()
        except Exception as e:
            if self.verbose:
                print(f"[SkillLoader] ⚠️ Failed to read {path}: {e}")
            return None
        
        # Match YAML frontmatter between --- markers
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
        if not match:
            # Try legacy format without frontmatter (just # heading)
            return self._parse_legacy_skill(path, content)
        
        frontmatter, body = match.groups()
        
        # Parse YAML-like frontmatter (simple key: value)
        metadata = {}
        for line in frontmatter.strip().split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip().strip("\"'")
        
        # Require name and description
        if "name" not in metadata:
            # Try to extract name from filename
            metadata["name"] = path.stem.replace("_usage", "").replace("_", " ").title()
        
        if "description" not in metadata:
            # Try to extract from first paragraph
            metadata["description"] = self._extract_first_paragraph(body)
        
        return {
            "name": metadata["name"],
            "description": metadata.get("description", ""),
            "body": body.strip(),
            "path": path,
            "dir": path.parent,
            "scope": metadata.get("scope", "strategic_skill"),
            "triggers": metadata.get("triggers", "").split(",") if metadata.get("triggers") else [],
        }
    
    def _parse_legacy_skill(self, path: Path, content: str) -> Optional[Dict]:
        """Parse legacy skill format (no YAML frontmatter)."""
        # Extract name from # heading
        name_match = re.search(r'^# (.+)$', content, re.MULTILINE)
        name = name_match.group(1).strip() if name_match else path.stem
        
        # Extract description from **Description:** or first paragraph
        desc_match = re.search(r'\*\*Description:\*\* ([^\n]+)', content)
        description = desc_match.group(1) if desc_match else self._extract_first_paragraph(content)
        
        # Extract triggers
        triggers_match = re.search(r'\*\*When to use:\*\* ([^\n]+)', content)
        triggers = triggers_match.group(1).split(", ") if triggers_match else []
        
        return {
            "name": name,
            "description": description,
            "body": content,
            "path": path,
            "dir": path.parent,
            "scope": "strategic_skill",
            "triggers": triggers,
        }
    
    def _extract_first_paragraph(self, content: str) -> str:
        """Extract first non-heading paragraph from markdown."""
        lines = content.strip().split('\n')
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#') and not line.startswith('*'):
                return line[:200]
        return ""
    
    def load_skills(self):
        """
        Scan skills directory and load all valid SKILL.md files.
        
        Supports two structures:
        1. Folder-based: skills/{skill_name}/SKILL.md (recommended)
        2. Flat: skills/{skill_name}.md (simpler)
        
        Only loads metadata at startup - body is loaded on-demand.
        """
        if not self.skills_dir.exists():
            if self.verbose:
                print(f"[SkillLoader] Skills directory not found: {self.skills_dir}")
            return
        
        # Load folder-based skills (skills/{name}/SKILL.md)
        for skill_dir in self.skills_dir.iterdir():
            if skill_dir.is_dir():
                skill_md = skill_dir / "SKILL.md"
                if skill_md.exists():
                    skill = self.parse_skill_md(skill_md)
                    if skill:
                        self.skills[skill["name"]] = skill
                        if self.verbose:
                            print(f"[SkillLoader] ✓ Loaded skill: {skill['name']} (folder)")
        
        # Load flat skills (skills/{name}.md)
        for skill_file in self.skills_dir.glob("*.md"):
            if skill_file.is_file():
                skill = self.parse_skill_md(skill_file)
                if skill and skill["name"] not in self.skills:
                    self.skills[skill["name"]] = skill
                    if self.verbose:
                        print(f"[SkillLoader] ✓ Loaded skill: {skill['name']} (flat)")
        
        if self.verbose:
            print(f"[SkillLoader] Loaded {len(self.skills)} skills total")
    
    def get_descriptions(self) -> str:
        """
        Generate skill descriptions for system prompt (Layer 1).
        
        This is minimal - only name and description, ~100 tokens per skill.
        Full content (Layer 2) is loaded only when requested.
        """
        if not self.skills:
            return "(no skills available)"
        
        return "\n".join(
            f"- **{name}**: {skill['description'][:150]}"
            for name, skill in self.skills.items()
        )
    
    def get_skill_content(self, name: str) -> Optional[str]:
        """
        Get full skill content for injection (Layer 2).
        
        This returns the complete SKILL.md body, plus hints about
        available resources (Layer 3).
        
        Returns None if skill not found.
        """
        # Try exact match
        skill = self.skills.get(name)
        
        # Try case-insensitive match
        if not skill:
            for skill_name, skill_data in self.skills.items():
                if skill_name.lower() == name.lower():
                    skill = skill_data
                    break
        
        if not skill:
            return None
        
        content = f"# Skill: {skill['name']}\n\n{skill['body']}"
        
        # List available resources (Layer 3 hints)
        resources = []
        skill_dir = skill.get("dir")
        if skill_dir and isinstance(skill_dir, Path):
            for folder, label in [
                ("scripts", "Scripts"),
                ("references", "References"),
                ("assets", "Assets")
            ]:
                folder_path = skill_dir / folder
                if folder_path.exists():
                    files = list(folder_path.glob("*"))
                    if files:
                        resources.append(f"{label}: {', '.join(f.name for f in files[:5])}")
        
        if resources:
            content += f"\n\n**Available resources in {skill_dir}:**\n"
            content += "\n".join(f"- {r}" for r in resources)
        
        return content
    
    def list_skills(self) -> List[str]:
        """Return list of available skill names."""
        return list(self.skills.keys())
    
    def get_skill_metadata(self, name: str) -> Optional[Dict]:
        """Get skill metadata without body (for filtering/matching)."""
        skill = self.skills.get(name)
        if skill:
            return {
                "name": skill["name"],
                "description": skill["description"],
                "scope": skill.get("scope", "strategic_skill"),
                "triggers": skill.get("triggers", []),
            }
        return None
    
    def find_matching_skills(self, context: str) -> List[str]:
        """
        Find skills that match the given context.
        
        Uses trigger keywords to suggest relevant skills.
        """
        context_lower = context.lower()
        matches = []
        
        for name, skill in self.skills.items():
            # Check triggers
            for trigger in skill.get("triggers", []):
                if trigger.lower() in context_lower:
                    matches.append(name)
                    break
            
            # Check description keywords
            if name.lower() in context_lower:
                if name not in matches:
                    matches.append(name)
        
        return matches
    
    def reload(self):
        """Reload all skills from disk."""
        self.skills.clear()
        self.load_skills()
    
    def to_dict(self) -> Dict[str, Dict]:
        """Export skills as dict (for context injection)."""
        return {
            name: {
                "name": skill["name"],
                "description": skill["description"],
                "instructions": skill["body"],
                "triggers": skill.get("triggers", []),
                "scope": skill.get("scope", "strategic_skill"),
            }
            for name, skill in self.skills.items()
        }
