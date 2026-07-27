REQUIRED_KEYS = {
    "summary",
    "current_state",
    "body_type_analysis",
    "training_recommendation",
    "diet_recommendation",
    "warning",
}

def validate_analysis_schema(result: dict):
    missing = REQUIRED_KEYS - result.keys()
    if missing:
        raise ValueError(f"Missing keys: {missing}")

    if not isinstance(result["training_recommendation"], dict):
        raise ValueError("training_recommendation must be object")

    if not isinstance(result["diet_recommendation"], dict):
        raise ValueError("diet_recommendation must be object")

