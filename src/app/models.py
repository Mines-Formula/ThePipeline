from dataclasses import dataclass, field
from typing import Optional
from collections import OrderedDict


@dataclass
class ConversionProgress:
    name: str
    progress: str
    exception: Optional[Exception] = None
    finished: bool = False
    metrics: dict = field(default_factory=dict)

    def pop_exception(self) -> Exception:
        if self.exception is None:
            raise IndexError("No present excepetion")

        exception = self.exception
        self.exception = None

        return exception


class LimitedDict(OrderedDict):
    def __init__(self, max_size=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_size = max_size

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if self.max_size is not None and len(self) > self.max_size:
            self.popitem(last=False)  # Remove the oldest item
