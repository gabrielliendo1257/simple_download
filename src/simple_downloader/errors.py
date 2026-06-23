from uuid import UUID


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

class JobNotFoundError(Exception):
    job_id: UUID

    def __init__(self, job_id: UUID, *args: object) -> None:
        super().__init__(*args)
        self.job_id = job_id
