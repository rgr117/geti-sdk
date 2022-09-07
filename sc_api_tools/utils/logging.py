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

import logging
import sys

default_level = logging.INFO
default_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def configure_basic_stdout_logging():
    """
    Set up default logging for the Public SDK. It logs to stdout using the
    default level and format
    """
    logging.root.handlers = []
    logging.basicConfig(
        handlers=[logging.StreamHandler(stream=sys.stdout)],
        level=default_level,
        format=default_format,
    )
