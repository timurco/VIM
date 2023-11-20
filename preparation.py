import os
import shutil
import gdown
import zipfile

from gdown.download import _get_session
from gdown.download_folder import _download_and_parse_google_drive_link, _get_directory_structure

SUB_FOLDER = './pretrained'

# Create a directory for pretrained models
if not os.path.exists(SUB_FOLDER):
    os.makedirs(SUB_FOLDER)

files = [
    "https://drive.google.com/drive/folders/1gYtZd66qeCA4JWdbguRaWecG90aqfvs5"
]

def is_zip_file(file_path):
    """Check if a file is a zip archive."""
    return zipfile.is_zipfile(file_path) and file_path.endswith('.zip')


def check_file_existence(local_file_path):
    """Check the existence of a file or its unpacked version."""
    # If the file is a zip archive, check for the corresponding directory
    if zipfile.is_zipfile(local_file_path):
        expected_dir = os.path.splitext(os.path.basename(local_file_path))[0]
        expected_dir_path = os.path.join(os.path.dirname(local_file_path), expected_dir)
        return os.path.exists(expected_dir_path)

    # If it's not a zip archive, simply check for the file's existence
    return os.path.exists(local_file_path)


# Downloading and unpacking files
for url in files:

    # downloaded_files = gdown.download_folder(url, output=SUB_FOLDER)
    sess = _get_session(use_cookies=False, proxy=None)
    return_code, gdrive_file = _download_and_parse_google_drive_link(sess, url, quiet=True)

    # Assuming gdrive_file is an object with file information
    directory_structure = _get_directory_structure(gdrive_file, SUB_FOLDER)

    for file_id, file_path in directory_structure:
        if file_id is None:  # folder
            if not os.path.exists(file_path):
                os.makedirs(file_path)
            continue

        if check_file_existence(file_path):
            print(f"File {file_path} already exists, skipping download.")
            continue

        filename = gdown.download(
            url="https://drive.google.com/uc?id=" + file_id,
            output=file_path
        )

        # Check if the file is a zip archive, and unzip
        if is_zip_file(file_path):
            # Create a folder with the same name as the file (excluding .zip extension)
            extract_folder = os.path.dirname(file_path)
            if not os.path.exists(extract_folder):
                os.makedirs(extract_folder)

            print(f"Unzipping {file_path} to {extract_folder}")
            # Unpack the zip file into this folder
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                zip_ref.extractall(extract_folder)

            # Delete the zip file after unpacking
            os.remove(file_path)
