"""Skill 注册表 - 管理所有可用的 Skill"""
import os
from typing import Dict, List, Optional
from .base import BaseSkill


class SkillRegistry:
    """Skill 注册表 - 管理所有可用 Skill"""
    
    def __init__(self, skills_dir: str = None):
        if skills_dir is None:
            # 默认路径
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            skills_dir = os.path.join(base_dir, "src", "skills")
        
        self.skills_dir = skills_dir
        self._skills: Dict[str, BaseSkill] = {}
        self._load_all_skills()
    
    def _load_all_skills(self):
        """加载所有 Skill"""
        if not os.path.exists(self.skills_dir):
            print(f"[SkillRegistry] Skills directory not found: {self.skills_dir}")
            return
        
        for item in os.listdir(self.skills_dir):
            skill_path = os.path.join(self.skills_dir, item)
            
            # 跳过 __pycache__ 和 __init__.py
            if item.startswith('_') or item.endswith('.py'):
                continue
            
            # 检查是否是 Skill 目录
            if os.path.isdir(skill_path):
                yaml_file = os.path.join(skill_path, "skill.yaml")
                if os.path.exists(yaml_file):
                    self._load_skill(skill_path)
    
    def _load_skill(self, skill_path: str):
        """加载单个 Skill"""
        try:
            skill = BaseSkill(skill_path)
            self._skills[skill.metadata.name] = skill
            print(f"[SkillRegistry] Loaded: {skill.metadata.name}")
        except Exception as e:
            print(f"[SkillRegistry] Failed to load {skill_path}: {e}")
    
    def get(self, name: str) -> Optional[BaseSkill]:
        """按名称获取 Skill"""
        return self._skills.get(name)
    
    def list_skills(self) -> List[str]:
        """列出所有 Skill 名称"""
        return list(self._skills.keys())
    
    def match(self, query: str, threshold: float = 0.5) -> Optional[BaseSkill]:
        """
        根据用户输入匹配最合适的 Skill
        
        Args:
            query: 用户输入
            threshold: 匹配阈值
            
        Returns:
            最匹配的 Skill 或 None
        """
        best_match = None
        best_score = 0.0
        
        for name, skill in self._skills.items():
            score = skill.matches(query)
            if score > best_score and score >= threshold:
                best_score = score
                best_match = skill
        
        return best_match
    
    def get_skill_info(self, name: str) -> Optional[Dict]:
        """获取 Skill 信息"""
        skill = self._skills.get(name)
        if skill:
            return {
                "name": skill.metadata.name,
                "description": skill.metadata.description,
                "version": skill.metadata.version,
                "capabilities": skill.metadata.capabilities,
                "templates": list(skill.templates.keys())
            }
        return None
    
    def list_all_skills_info(self) -> List[Dict]:
        """列出所有 Skill 的信息"""
        return [self.get_skill_info(name) for name in self.list_skills()]
