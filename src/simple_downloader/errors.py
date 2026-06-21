class SourceUnvaliableError(Exception):
    executable_name: str

    def __init__(self, executable_name: str) -> None:
        super().__init__()
        self.executable_name = executable_name

class ProcessError(Exception):
    stderr: str
    def __init__(self, stderr: str, *args: object) -> None:
        super().__init__(*args)
        self.stderr = stderr
