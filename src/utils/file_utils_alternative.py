from typing import Tuple, Optional, Set, List, Dict, Any
from werkzeug.utils import secure_filename
from werkzeug.datastructures import FileStorage
import os
import hashlib
import magic
import shutil
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class FileManager:
    def __init__(
        self,
        upload_dir: str,
        allowed_extensions: Set[str],
        max_file_size: int
    ):
        self.upload_dir = upload_dir
        self.allowed_extensions = allowed_extensions
        self.max_file_size = max_file_size
        self.mime = magic.Magic(mime=True)

        # Create upload directory if it doesn't exist
        os.makedirs(upload_dir, exist_ok=True)

    def validate_file(self, file: FileStorage) -> Tuple[bool, Optional[str]]:
        """
        Validate uploaded file.

        Args:
            file: FileStorage object from Flask

        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            # Check if file exists
            if not file:
                return False, "No file provided"

            # Check filename
            if file.filename == '':
                return False, "No selected file"

            # Check file extension
            if not self._allowed_extension(file.filename):
                return False, f"File type not allowed. Allowed types: {', '.join(self.allowed_extensions)}"

            # Store current position
            current_position = file.tell()

            # Check file size
            file.seek(0, os.SEEK_END)
            size = file.tell()
            file.seek(current_position)  # Restore position

            if size > self.max_file_size:
                max_mb = self.max_file_size / (1024 * 1024)
                return False, f"File too large. Maximum size: {max_mb}MB"

            # Check MIME type
            try:
                file_content = file.read(2048)
                file.seek(current_position)  # Restore position
                mime_type = self.mime.from_buffer(file_content)

                if not self._allowed_mime_type(mime_type):
                    return False, f"Invalid file type: {mime_type}"
            except Exception as e:
                return False, f"Error checking file type: {str(e)}"

            return True, None

        except Exception as e:
            logger.error(f"File validation error: {str(e)}", exc_info=True)
            return False, f"Error validating file: {str(e)}"

    def save_uploaded_file(self, file: FileStorage, filename: str) -> Tuple[str, str]:
        """
        Save uploaded file and calculate hash.

        Args:
            file: FileStorage object
            filename: Original filename

        Returns:
            Tuple containing (file_path, file_hash)
        """
        try:
            # Generate safe filename
            safe_filename = secure_filename(filename)
            file_path = os.path.join(self.upload_dir, safe_filename)

            # Calculate hash while saving
            file_hash = hashlib.sha256()
            file.seek(0)  # Ensure we're at the start

            with open(file_path, 'wb') as f:
                while chunk := file.read(8192):
                    file_hash.update(chunk)
                    f.write(chunk)

            return file_path, file_hash.hexdigest()

        except Exception as e:
            logger.error(f"Error saving file {filename}: {str(e)}")
            raise

    def cleanup_temp_files(self, *file_paths: str):
        """
        Clean up temporary files.

        Args:
            file_paths: Paths to files or directories to clean up
        """
        for path in file_paths:
            try:
                if os.path.exists(path):
                    if os.path.isfile(path):
                        os.remove(path)
                    elif os.path.isdir(path):
                        shutil.rmtree(path)
            except Exception as e:
                logger.warning(f"Error cleaning up {path}: {str(e)}")

    def _allowed_extension(self, filename: str) -> bool:
        """Check if file extension is allowed."""
        return '.' in filename and \
            filename.rsplit('.', 1)[1].lower() in self.allowed_extensions

    def _allowed_mime_type(self, mime_type: str) -> bool:
        """Check if MIME type is allowed."""
        allowed_mimes = {
            'application/pdf',
            'application/msword',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/vnd.ms-excel',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'image/jpeg',
            'image/png'
        }
        return mime_type in allowed_mimes

class BatchFileManager(FileManager):
    """Extended FileManager for batch operations."""

    def __init__(
        self,
        upload_dir: str,
        allowed_extensions: Set[str],
        max_file_size: int,
        max_batch_size: int = 100
    ):
        super().__init__(upload_dir, allowed_extensions, max_file_size)
        self.max_batch_size = max_batch_size

    def process_batch(
        self,
        files: List[Tuple[str, bytes]],
        prefix: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Process a batch of files.

        Args:
            files: List of (filename, content) tuples
            prefix: Optional prefix for saved files

        Returns:
            List of dictionaries containing file information
        """
        if len(files) > self.max_batch_size:
            raise ValueError(
                f"Batch size {len(files)} exceeds maximum {self.max_batch_size}"
            )

        results = []
        temp_files = []

        try:
            for filename, content in files:
                # Save file temporarily
                with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                    temp_file.write(content)
                    temp_file.flush()
                    temp_files.append(temp_file.name)

                    # Move to final location
                    file_path, file_hash = self.save_uploaded_file(
                        open(temp_file.name, 'rb'),
                        filename,
                        prefix
                    )

                    # Get file info
                    file_info = self.get_file_info(file_path)
                    results.append({
                        'original_filename': filename,
                        'saved_path': file_path,
                        'hash': file_hash,
                        **file_info
                    })

            return results

        finally:
            # Cleanup temporary files
            self.cleanup_temp_files(*temp_files)

    def validate_batch(self, files: List[Tuple[str, bytes]]) -> List[Dict[str, Any]]:
        """
        Validate a batch of files before processing.

        Args:
            files: List of (filename, content) tuples

        Returns:
            List of dictionaries containing validation results
        """
        validation_results = []

        for filename, content in files:
            result = {
                'filename': filename,
                'valid': True,
                'errors': []
            }

            # Check file size
            if len(content) > self.max_file_size:
                result['valid'] = False
                result['errors'].append(
                    f'File size exceeds maximum of {self.max_file_size} bytes'
                )

            # Check extension
            ext = os.path.splitext(filename)[1].lower().lstrip('.')
            if ext not in self.allowed_extensions:
                result['valid'] = False
                result['errors'].append(f'Extension .{ext} not allowed')

            # Check MIME type
            mime_type = magic.from_buffer(content, mime=True)
            if not self._allowed_mime_type(mime_type):
                result['valid'] = False
                result['errors'].append(f'MIME type {mime_type} not allowed')

            validation_results.append(result)

        return validation_results

    def get_file_info(self, file_path: str) -> Dict[str, Any]:
        """Get file information."""
        stat = os.stat(file_path)
        return {
            'size': stat.st_size,
            'created': datetime.fromtimestamp(stat.st_ctime).isoformat(),
            'modified': datetime.fromtimestamp(stat.st_mtime).isoformat(),
            'mime_type': self.mime.from_file(file_path)
        }

def create_nested_directory(base_path: str, *paths: str) -> str:
    """Create nested directory structure."""
    full_path = os.path.join(base_path, *paths)
    os.makedirs(full_path, exist_ok=True)
    return full_path

def get_directory_size(directory: str) -> int:
    """Calculate total size of directory in bytes."""
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(directory):
        for filename in filenames:
            file_path = os.path.join(dirpath, filename)
            total_size += os.path.getsize(file_path)
    return total_size

def cleanup_old_files(
    directory: str,
    max_age_days: int,
    exclude_patterns: Optional[List[str]] = None
):
    """Remove files older than specified age."""
    now = datetime.now()
    max_age = now.timestamp() - (max_age_days * 24 * 60 * 60)

    for dirpath, dirnames, filenames in os.walk(directory):
        for filename in filenames:
            if exclude_patterns and any(pattern in filename for pattern in exclude_patterns):
                continue

            file_path = os.path.join(dirpath, filename)
            if os.path.getctime(file_path) < max_age:
                try:
                    os.remove(file_path)
                    logger.info(f"Removed old file: {file_path}")
                except Exception as e:
                    logger.error(f"Error removing {file_path}: {str(e)}")