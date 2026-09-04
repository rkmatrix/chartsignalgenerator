"""CPU path for replay/walk-forward. GPU is not required and is not used."""


def available_device() -> str:
    return "cpu"
