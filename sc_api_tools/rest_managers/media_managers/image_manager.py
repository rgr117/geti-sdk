import glob
import os
from typing import List, Union

import numpy as np

from .media_manager import BaseMediaManager, MEDIA_SUPPORTED_FORMAT_MAPPING
from sc_api_tools.data_models import MediaType, MediaList, Image
from sc_api_tools.rest_converters import MediaRESTConverter


class ImageManager(BaseMediaManager[Image]):
    """
    Class to manage image uploads and downloads for a certain project
    """

    _MEDIA_TYPE = MediaType.IMAGE

    def get_all_images(self) -> MediaList[Image]:
        """
        Get the ID's and filenames of all images in the project

        :return: MediaList containing all Image entities in the project
        """
        return self._get_all()

    def upload_image(self, image: Union[np.ndarray, str, os.PathLike]) -> Image:
        """
        Upload an image file to the server

        :param filepath_to_image: full path to the image on disk
        :return: String containing the unique ID of the image, generated by Sonoma
            Creek
        """
        if isinstance(image, (str, os.PathLike)):
            image_dict = self._upload(image)
        elif isinstance(image, np.ndarray):
            image_dict = self.upload_bytes(cv2.imencode('.jpg', image)[1].tobytes())
        return MediaRESTConverter.from_dict(
            input_dict=image_dict, media_type=Image
        )

    def upload_folder(
            self, path_to_folder: str, n_images: int = -1, return_all: bool = True
    ) -> MediaList[Image]:
        """
        Uploads all images in a folder to the project. Returns a MediaList containing
        all images in the project after upload.

        :param path_to_folder: Folder with images to upload
        :param n_images: Number of images to upload from folder
        :param return_all: Set to True to return a list of all images in the project
            after the upload. Set to False to return a list containing only the images
            uploaded with the current call to this method. Defaults to True
        :return: MediaList containing all image's in the project
        """
        return self._upload_folder(
            path_to_folder=path_to_folder, n_media=n_images, return_all=return_all
        )

    def download_all(self, path_to_folder: str) -> None:
        """
        Download all images in a project to a folder on the local disk.

        :param path_to_folder: path to the folder in which the images should be saved
        """
        self._download_all(path_to_folder)

    def upload_from_list(
        self,
        path_to_folder: str,
        image_names: List[str],
        extension_included: bool = False,
        n_images: int = -1,
        return_all: bool = True
    ):
        """
        From a folder containing images `path_to_folder`, this method uploads only
        those images that have their filenames included in the `image_names` list.

        :param path_to_folder: Folder containing the images
        :param image_names: List of names of the images that should be uploaded
        :param extension_included: Set to True if the extension of the image is
            included in the name, for each image in the image_names list. Defaults to
            False
        :param n_images: Number of images to upload from the list
        :param return_all: Set to True to return a list of all images in the project
            after the upload. Set to False to return a list containing only the images
            uploaded with the current call to this method. Defaults to True
        :return: Dictionary containing a mapping between the ID's of the images and
            their filenames (excluding extensions). NOTE: Filenames are used as keys,
            ID's as values
        """
        image_filepaths: List[str] = []
        if n_images > len(image_names) or n_images == -1:
            n_to_upload = len(image_names)
        else:
            n_to_upload = n_images
        for image_name in image_names[0:n_to_upload]:
            if not extension_included:
                media_formats = MEDIA_SUPPORTED_FORMAT_MAPPING[self._MEDIA_TYPE]
                matches: List[str] = []
                for media_extension in media_formats:
                    matches += glob.glob(
                        os.path.join(
                            path_to_folder, '**', f'{image_name}{media_extension}'
                        ),
                        recursive=True
                    )
            else:
                matches = glob.glob(
                    os.path.join(path_to_folder, '**', image_name), recursive=True
                )
            if not matches:
                raise ValueError(
                    f"No matching file found for image with name {image_name}"
                )
            elif len(matches) != 1:
                raise ValueError(
                    f"Multiple files found for image with name {image_name}: {matches}"
                )
            image_filepaths.append(matches[0])
        return self._upload_loop(filepaths=image_filepaths, return_all=return_all)

    def delete_images(self, images: MediaList[Image]) -> bool:
        """
        Deletes all Image entities in `images` from the project

        :param images: List of Image entities to delete
        :return: True if all images on the list were deleted successfully,
            False otherwise
        """
        return self._delete_media(media_list=images)
