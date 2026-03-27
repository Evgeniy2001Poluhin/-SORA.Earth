from typing import Dict, Any
from app.validators import ProjectInput


def evaluate_project_esg(project: ProjectInput, base_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Обёртка-адаптер: принимает ProjectInput и уже посчитанный result (dict),
    ничего не меняет сейчас.
    """
    return base_result
