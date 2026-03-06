"""Skill 基类 - 定义 Skill 的核心接口和数据结构"""
import os
import yaml
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Optional


@dataclass
class SkillTemplate:
    """Skill 代码模板"""
    name: str
    description: str
    code: str


@dataclass
class SkillMetadata:
    """Skill 元数据"""
    name: str
    description: str
    version: str
    triggers: List[str]  # 触发关键词
    capabilities: List[str]
    templates: List[Dict]
    dependencies: List[str]
    author: str = ""
    tags: List[str] = None
    
    def __post_init__(self):
        if self.tags is None:
            self.tags = []


class BaseSkill(ABC):
    """Skill 基类"""
    
    def __init__(self, skill_path: str):
        self.skill_path = skill_path
        self.metadata = self._load_metadata()
        self.prompt = self._load_prompt()
        self.templates = self._load_templates()
    
    def _load_metadata(self) -> SkillMetadata:
        """加载 skill.yaml"""
        yaml_path = os.path.join(self.skill_path, "skill.yaml")
        if not os.path.exists(yaml_path):
            raise FileNotFoundError(f"skill.yaml not found at {yaml_path}")
        
        with open(yaml_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        
        return SkillMetadata(**data)
    
    def _load_prompt(self) -> str:
        """加载 prompt.md"""
        prompt_path = os.path.join(self.skill_path, "prompt.md")
        if os.path.exists(prompt_path):
            with open(prompt_path, 'r', encoding='utf-8') as f:
                return f.read()
        return ""
    
    def _load_templates(self) -> Dict[str, SkillTemplate]:
        """加载代码模板"""
        templates = {}
        templates_dir = os.path.join(self.skill_path, "templates")
        
        if os.path.exists(templates_dir):
            for filename in os.listdir(templates_dir):
                if filename.endswith('.py'):
                    name = filename[:-3]
                    filepath = os.path.join(templates_dir, filename)
                    with open(filepath, 'r', encoding='utf-8') as f:
                        code = f.read()
                    
                    # 从模板元数据中获取描述
                    description = f"Template {name}"
                    if self.metadata.templates:
                        for tmpl_info in self.metadata.templates:
                            if tmpl_info.get('name') == name:
                                description = tmpl_info.get('description', description)
                                break
                    
                    templates[name] = SkillTemplate(
                        name=name,
                        description=description,
                        code=code
                    )
        
        return templates
    
    def get_system_prompt(self) -> str:
        """获取完整的系统提示词"""
        base_prompt = self.prompt
        
        # 添加模板信息
        if self.templates:
            base_prompt += "\n\n## 可用代码模板\n\n"
            for name, template in self.templates.items():
                base_prompt += f"### {name}\n{template.description}\n\n"
                base_prompt += f"```python\n{template.code}\n```\n\n"
        
        return base_prompt
    
    def get_template(self, name: str) -> Optional[str]:
        """获取指定模板代码"""
        if name in self.templates:
            return self.templates[name].code
        return None
    
    def matches(self, query: str) -> float:
        """
        检查用户输入是否匹配此 Skill
        返回匹配度分数 (0.0 - 1.0)
        """
        query_lower = query.lower()
        
        # 检查触发关键词（精确匹配）
        for trigger in self.metadata.triggers:
            if trigger.lower() in query_lower:
                return 1.0
        
        # 检查能力描述（模糊匹配）
        for cap in self.metadata.capabilities:
            if cap.lower() in query_lower:
                return 0.8
        
        return 0.0
