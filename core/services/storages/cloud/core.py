class CloudStorageServiceInterface:
    """
    Interface for storage services
    """

    def __init__(self):
        pass

    def upload_file(self, key, file_path=None, headers=None, binary=False, metadata=None):  # pylint: disable=too-many-arguments
        """
        Uploads binary file object to key given file_path
        """

    def upload_base64(self, doc_base64, file_name, append_extension=True, public_read=False, headers=None):  # pylint: disable=too-many-arguments
        """
        Uploads base64 file content to file_name
        """

    def url_for(self, file_path):
        """
        Returns signed url for file_path
        """

    def public_url_for(self, file_path):
        """
        Returns public (or unsigned) url for file_path
        """

    def exists(self, key):
        """
        Checks if key (object) exists
        """

    def has_path(self, prefix='/', delimiter='/'):
        """
        Checks if path exists
        """

    def get_last_key_from_path(self, prefix='/', delimiter='/'):
        """
        Returns last key from path
        """

    def delete_objects(self, path):
        """
        Deletes all objects in path
        """

    def remove(self, key):
        """
        Removes object
        """
