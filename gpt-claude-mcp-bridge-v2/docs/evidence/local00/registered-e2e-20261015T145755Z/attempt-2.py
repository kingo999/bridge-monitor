```python
def clamp(value, lower, upper):
    if lower > upper:
        raise ValueError("lower should not be greater than upper")
    return max(lower, min(value, upper))
```