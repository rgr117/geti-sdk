# Copyright (C) 2022 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions
# and limitations under the License.

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Type, TypeVar, Union

from tqdm.auto import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm

from geti_sdk.annotation_readers import AnnotationReader
from geti_sdk.data_models import (
    AnnotationKind,
    AnnotationScene,
    Image,
    Project,
    Video,
    VideoFrame,
)
from geti_sdk.data_models.containers.media_list import MediaList
from geti_sdk.data_models.media import MediaInformation
from geti_sdk.data_models.project import Dataset
from geti_sdk.http_session import GetiRequestException, GetiSession
from geti_sdk.rest_clients.dataset_client import DatasetClient
from geti_sdk.rest_converters import AnnotationRESTConverter
from geti_sdk.rest_converters.annotation_rest_converter import (
    NormalizedAnnotationRESTConverter,
)

AnnotationReaderType = TypeVar("AnnotationReaderType", bound=AnnotationReader)
MediaType = TypeVar("MediaType", Image, Video)


class BaseAnnotationClient:
    """
    Class to up- or download annotations for 2d media to an existing project.
    """

    def __init__(
        self,
        session: GetiSession,
        workspace_id: str,
        project: Project,
        annotation_reader: Optional[AnnotationReaderType] = None,
    ):
        self.session = session
        self.workspace_id = workspace_id
        self.annotation_reader = annotation_reader
        self._project = project
        if annotation_reader is None:
            label_mapping = None
        else:
            label_mapping = self.__get_label_mapping(project)
        self._label_mapping = label_mapping
        self._dataset_client = DatasetClient(
            session=session, project=project, workspace_id=workspace_id
        )

    def _get_all_media_by_type(
        self, media_type: Type[MediaType]
    ) -> MediaList[MediaType]:
        """
        Get a list holding all media entities of type `media_type` in the project.

        :param media_type: Type of media item to retrieve. Can be 'Image' or 'Video'
        :return: MediaList holding all media of a certain type in the project
        """
        datasets = self._dataset_client.get_all_datasets()
        media_list = MediaList[media_type]([])
        for dataset in datasets:
            media_list.extend(
                self._get_all_media_in_dataset_by_type(
                    media_type=media_type, dataset=dataset
                )
            )
        return media_list

    def _get_all_media_in_dataset_by_type(
        self, media_type: Type[MediaType], dataset: Dataset
    ) -> MediaList[MediaType]:
        """
        Return a list of all media items of type `media_type` in the dataset `dataset`

        :param media_type: Type of media item to retrieve. Can be 'Image' or 'Video'
        :param dataset: Dataset to retrieve media items for
        :return: List of all media items of a certain media_type in the dataset
        """
        if media_type == Image:
            media_name = "images"
        elif media_type == Video:
            media_name = "videos"
        else:
            raise ValueError(f"Invalid media type specified: {media_type}.")
        get_media_url = (
            f"workspaces/{self.workspace_id}/projects/{self._project.id}"
            f"/datasets/{dataset.id}/media/"
            f"{media_name}?top=500"
        )
        response = self.session.get_rest_response(url=get_media_url, method="GET")
        total_number_of_media: int = response["media_count"][media_name]
        raw_media_list: List[Dict[str, Any]] = []
        while len(raw_media_list) < total_number_of_media:
            for media_item_dict in response["media"]:
                raw_media_list.append(media_item_dict)
            if "next_page" in response.keys():
                response = self.session.get_rest_response(
                    url=response["next_page"], method="GET"
                )
        return MediaList.from_rest_list(
            rest_input=raw_media_list, media_type=media_type
        )

    def __get_label_mapping(self, project: Project) -> Dict[str, str]:
        """
        Get the mapping of the label names to the label ids for the project

        :param project:
        :return: Dictionary containing the label names as keys and the label ids as
            values
        """
        source_label_names = self.annotation_reader.get_all_label_names()
        project_label_mapping = project.pipeline.label_id_to_name_mapping
        project_label_name_to_id_mapping = {
            name: id_ for (id_, name) in project_label_mapping.items()
        }
        for source_label_name in source_label_names:
            if source_label_name not in project_label_name_to_id_mapping:
                raise ValueError(
                    f"Found label {source_label_name} in source labels, but this "
                    f"label is not in the project labels."
                )
        return project_label_name_to_id_mapping

    @property
    def label_mapping(self) -> Dict[str, str]:
        """
        Return a dictionary with label names as keys and label ids as values.

        :return:
        """
        if self.annotation_reader is not None:
            if self._label_mapping is None:
                self._label_mapping = self.__get_label_mapping(self._project)
            return self._label_mapping
        else:
            raise ValueError(
                "Unable to get label mapping for this annotation client, no "
                "annotation reader has been defined."
            )

    def _upload_annotation_for_2d_media_item(
        self,
        media_item: Union[Image, VideoFrame],
        annotation_scene: Optional[AnnotationScene] = None,
    ) -> AnnotationScene:
        """
        Upload a new annotation for an image or video frame to the cluster. This will
        overwrite any current annotations for the media item.

        If an `annotation_scene` is passed, this annotation will be applied to the
        media_item.

        If `annotation_scene` is left as None and an AnnotationReader is defined for
        the AnnotationClient, this method will read the annotation for
        the media item from the AnnotationReader.

        If `annotation_scene` is left as None and no AnnotationReader is defined for
        the AnnotationClient, this method will raise a ValueError

        :param media_item: Image or VideoFrame to upload annotation for
        :param annotation_scene: Optional AnnotationScene to apply to the media_item.
            If left as None, this method will read the annotation data using the
            AnnotationReader
        :return: AnnotationScene that was uploaded
        """
        if annotation_scene is not None:
            scene_to_upload = annotation_scene.apply_identifier(
                media_identifier=media_item.identifier
            )
        else:
            if self.annotation_reader is not None:
                scene_to_upload = self._read_2d_media_annotation_from_source(
                    media_item=media_item
                )
            else:
                raise ValueError(
                    "You attempted to upload an annotation for a media item, but no "
                    "annotation data was passed directly and no annotation reader was "
                    "defined for the AnnotationClient. Therefore, the "
                    "AnnotationClient is unable to upload any annotation data."
                )
        if scene_to_upload.has_data:
            scene_to_upload.prepare_for_post()
            if self.session.version.is_sc_mvp or self.session.version.is_sc_1_1:
                rest_data = NormalizedAnnotationRESTConverter.to_normalized_dict(
                    scene_to_upload,
                    deidentify=False,
                    image_width=media_item.media_information.width,
                    image_height=media_item.media_information.height,
                )
            else:
                rest_data = AnnotationRESTConverter.to_dict(
                    scene_to_upload, deidentify=False
                )
            rest_data.pop("kind")
            self.session.get_rest_response(
                url=f"{media_item.base_url}/annotations", method="POST", data=rest_data
            )
        return scene_to_upload

    def annotation_scene_from_rest_response(
        self, response_dict: Dict[str, Any], media_information: MediaInformation
    ) -> AnnotationScene:
        """
        Convert a dictionary with annotation data obtained from the Intel® Geti™
        /annotations rest endpoint into an annotation scene.

        :param response_dict: Dictionary containing the annotation data
        :param media_information: MediaInformation about the media item to which the
            annotation applies
        :return: AnnotationScene object corresponding to the data in the response_dict
        """
        if self.session.version.is_sc_mvp or self.session.version.is_sc_1_1:
            annotation_scene = (
                NormalizedAnnotationRESTConverter.normalized_annotation_scene_from_dict(
                    response_dict,
                    image_width=media_information.width,
                    image_height=media_information.height,
                )
            )
        else:
            annotation_scene = AnnotationRESTConverter.from_dict(response_dict)
        return annotation_scene

    def _append_annotation_for_2d_media_item(
        self, media_item: Union[Image, VideoFrame]
    ) -> AnnotationScene:
        """
        Add an annotation to the existing annotations for the `media_item`.

        :param media_item: Image or VideoFrame to append the annotation for
        :return: Returns the response of the REST endpoint to post the updated
            annotation
        """
        new_annotation_scene = self._read_2d_media_annotation_from_source(
            media_item=media_item, preserve_shape_for_global_labels=True
        )
        annotation_scene = self._get_latest_annotation_for_2d_media_item(media_item)
        if annotation_scene is None:
            logging.info(
                f"No existing annotation found for {str(media_item.type)} named "
                f"{media_item.name}"
            )
            annotation_scene = AnnotationScene(
                media_identifier=media_item.identifier,
                annotations=[],
                kind=AnnotationKind.ANNOTATION.value,
            )
        annotation_scene.extend(new_annotation_scene.annotations)

        if annotation_scene.has_data:
            if self.session.version.is_sc_mvp or self.session.version.is_sc_1_1:
                rest_data = NormalizedAnnotationRESTConverter.to_normalized_dict(
                    annotation_scene,
                    deidentify=False,
                    image_width=media_item.media_information.width,
                    image_height=media_item.media_information.height,
                )
            else:
                rest_data = AnnotationRESTConverter.to_dict(
                    annotation_scene, deidentify=False
                )
            rest_data.pop("kind", None)
            rest_data.pop("annotation_state_per_task", None)
            rest_data.pop("id", None)
            response = self.session.get_rest_response(
                url=f"{media_item.base_url}/annotations", method="POST", data=rest_data
            )
            return AnnotationRESTConverter.from_dict(response)
        else:
            return annotation_scene

    def _get_latest_annotation_for_2d_media_item(
        self, media_item: Union[Image, VideoFrame]
    ) -> Optional[AnnotationScene]:
        """
        Retrieve the latest annotation for an image or video frame from the cluster.
        If no annotation is available, this method returns None

        :param media_item: Image or VideoFrame to retrieve the annotations for
        :return: Dictionary containing the annotations data
        """
        try:
            response = self.session.get_rest_response(
                url=f"{media_item.base_url}/annotations/latest", method="GET"
            )
        except GetiRequestException as error:
            if error.status_code in [204, 404]:
                return None
            else:
                raise error
        return self.annotation_scene_from_rest_response(
            response, media_item.media_information
        )

    def _read_2d_media_annotation_from_source(
        self,
        media_item: Union[Image, VideoFrame],
        preserve_shape_for_global_labels: bool = False,
    ) -> AnnotationScene:
        """
        Retrieve the annotation for the media_item, and return it in the
        proper format to be sent to the GETi /annotations endpoint. This method uses the
        `self.annotation_reader` to get the annotation data.

        :param media_item: MediaItem to read the annotation for
        :return: Dictionary containing the annotation, in GETi format
        """
        annotation_list = self.annotation_reader.get_data(
            filename=media_item.name,
            label_name_to_id_mapping=self.label_mapping,
            media_information=media_item.media_information,
            preserve_shape_for_global_labels=preserve_shape_for_global_labels,
        )
        return AnnotationRESTConverter.from_dict(
            {
                "media_identifier": media_item.identifier,
                "annotations": annotation_list,
                "kind": AnnotationKind.ANNOTATION,
            }
        )

    def _download_annotations_for_2d_media_list(
        self,
        media_list: Union[MediaList[Image], MediaList[VideoFrame]],
        path_to_folder: str,
        append_media_uid: bool = False,
        verbose: bool = True,
    ) -> float:
        """
        Download annotations from the server to a target folder on disk.

        :param media_list: List of images or video frames to download the annotations
            for
        :param path_to_folder: Folder to save the annotations to
        :param append_media_uid: True to append the UID of a media item to the
            annotation filename (separated from the original filename by an underscore,
             i.e. '{filename}_{media_id}').
        :return: Returns the time elapsed to download the annotations, in seconds
        """
        path_to_annotations_folder = os.path.join(path_to_folder, "annotations")
        os.makedirs(path_to_annotations_folder, exist_ok=True, mode=0o770)
        if media_list.media_type == Image:
            media_name = "image"
            media_name_plural = "images"
        elif media_list.media_type == VideoFrame:
            media_name = "video frame"
            media_name_plural = "video frames"
        else:
            raise ValueError(
                "Invalid media type found in media_list, unable to download "
                "annotations."
            )
        if verbose:
            logging.info(
                f"Starting annotation download... saving annotations for "
                f"{len(media_list)} {media_name_plural} to folder "
                f"{path_to_annotations_folder}"
            )
        t_start = time.time()
        download_count = 0
        skip_count = 0
        tqdm_prefix = f"Downloading {media_name} annotations"
        with logging_redirect_tqdm(tqdm_class=tqdm):
            for media_item in tqdm(media_list, desc=tqdm_prefix):
                annotation_scene = self._get_latest_annotation_for_2d_media_item(
                    media_item
                )
                if annotation_scene is None:
                    if verbose:
                        logging.info(
                            f"Unable to retrieve latest annotation for {media_name} "
                            f"{media_item.name}. Skipping this {media_name}"
                        )
                    skip_count += 1
                    continue
                kind = annotation_scene.kind
                if kind != AnnotationKind.ANNOTATION:
                    if verbose:
                        logging.info(
                            f"Received invalid annotation of kind {kind} for {media_name} "
                            f"with name{media_item.name}"
                        )
                    skip_count += 1
                    continue
                export_data = AnnotationRESTConverter.to_dict(annotation_scene)

                base_media_item_name = os.path.basename(media_item.name)
                filename = base_media_item_name + ".json"
                if append_media_uid:
                    if isinstance(media_item, Image):
                        filename = f"{base_media_item_name}_{media_item.id}.json"
                    elif isinstance(media_item, VideoFrame):
                        if media_item.video_name is not None:
                            base_video_name = os.path.basename(media_item.video_name)
                            filename = (
                                f"{base_video_name}_"
                                f"{media_item.media_information.video_id}_frame_"
                                f"{media_item.media_information.frame_index}.json"
                            )
                        else:
                            video_name = base_media_item_name.split("_frame_")[0]
                            filename = (
                                f"{video_name}_"
                                f"{media_item.media_information.video_id}_frame_"
                                f"{media_item.media_information.frame_index}.json"
                            )
                    else:
                        raise TypeError(
                            f"Received invalid media item of type {type(media_item)}."
                        )

                annotation_path = os.path.join(path_to_annotations_folder, filename)

                with open(annotation_path, "w") as f:
                    json.dump(export_data, f, indent=4)
                download_count += 1
        t_elapsed = time.time() - t_start
        if download_count > 0:
            msg = (
                f"Downloaded {download_count} annotations to folder "
                f"{path_to_annotations_folder} in {t_elapsed:.1f} seconds."
            )
        else:
            msg = "No annotations were downloaded."
        if skip_count > 0:
            msg = (
                msg + f" Was unable to retrieve annotations for {skip_count} "
                f"{media_name_plural}, these {media_name_plural} were skipped."
            )
        if verbose:
            logging.info(msg)
        return t_elapsed
