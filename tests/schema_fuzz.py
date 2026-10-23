"""Случайный ответ, допустимый по JSON-схеме (для офлайн-проверки Claude-игрока)."""
import random


def random_instance(schema, rng: random.Random):
    if "enum" in schema:
        return rng.choice(schema["enum"])
    if "anyOf" in schema:
        return random_instance(rng.choice(schema["anyOf"]), rng)
    kind = schema["type"]
    if kind == "object":
        return {k: random_instance(v, rng) for k, v in schema["properties"].items()
                if k in schema.get("required", []) or rng.random() < 0.7}
    if kind == "array":
        n = rng.randint(schema.get("minItems", 0), schema.get("maxItems", 3))
        return [random_instance(schema["items"], rng) for _ in range(n)]
    if kind == "integer":
        return rng.randint(schema.get("minimum", 0), min(3, schema.get("maximum", 3)))
    if kind == "boolean":
        return rng.random() < 0.5
    if kind == "string":
        return "text"
    raise ValueError(kind)
