import os
from src.core.tools import tool

@tool
def read_file(filepath: str) -> str:
    """Reads and returns the contents of a file.
    
    Args:
        filepath: The absolute or relative path to the file.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        return f.read()

@tool
def write_file(filepath: str, content: str) -> str:
    """Writes content to a file, overwriting if it exists.
    
    Args:
        filepath: The absolute or relative path to the file.
        content: The content to write.
    """
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    return f"Successfully wrote to {filepath}"

@tool
def list_directory(path: str) -> list[str]:
    """Returns a list of files and directories in the given path.
    
    Args:
        path: The directory path to list.
    """
    return os.listdir(path)
