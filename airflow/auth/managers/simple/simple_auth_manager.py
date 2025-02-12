#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
from __future__ import annotations

import json
import os
import random
from collections import namedtuple
from enum import Enum
from typing import TYPE_CHECKING, Any

from flask import session, url_for
from termcolor import colored

from airflow.auth.managers.base_auth_manager import BaseAuthManager
from airflow.auth.managers.simple.user import SimpleAuthManagerUser
from airflow.auth.managers.simple.views.auth import SimpleAuthManagerAuthenticationViews
from airflow.configuration import AIRFLOW_HOME, conf

if TYPE_CHECKING:
    from flask_appbuilder.menu import MenuItem

    from airflow.auth.managers.base_auth_manager import ResourceMethod
    from airflow.auth.managers.models.resource_details import (
        AccessView,
        AssetDetails,
        ConfigurationDetails,
        ConnectionDetails,
        DagAccessEntity,
        DagDetails,
        PoolDetails,
        VariableDetails,
    )
    from airflow.www.extensions.init_appbuilder import AirflowAppBuilder


class SimpleAuthManagerRole(namedtuple("SimpleAuthManagerRole", "name order"), Enum):
    """
    List of pre-defined roles in simple auth manager.

    The first attribute defines the name that references this role in the config.
    The second attribute defines the order between roles. The role with order X means it grants access to
    resources under its umbrella and all resources under the umbrella of roles of lower order
    """

    # VIEWER role gives all read-only permissions
    VIEWER = "VIEWER", 0

    # USER role gives viewer role permissions + access to DAGs
    USER = "USER", 1

    # OP role gives user role permissions + access to connections, config, pools, variables
    OP = "OP", 2

    # ADMIN role gives all permissions
    ADMIN = "ADMIN", 3


class SimpleAuthManager(BaseAuthManager[SimpleAuthManagerUser]):
    """
    Simple auth manager.

    Default auth manager used in Airflow. This auth manager should not be used in production.
    This auth manager is very basic and only intended for development and testing purposes.
    """

    # Cache containing the password associated to a username
    passwords: dict[str, str] = {}

    # TODO: Needs to be deleted when Airflow 2 legacy UI is gone
    appbuilder: AirflowAppBuilder | None = None

    @staticmethod
    def get_generated_password_file() -> str:
        return os.path.join(
            os.getenv("AIRFLOW_AUTH_MANAGER_CREDENTIAL_DIRECTORY", AIRFLOW_HOME),
            "simple_auth_manager_passwords.json.generated",
        )

    @staticmethod
    def get_users() -> list[dict[str, str]]:
        users = [u.split(":") for u in conf.getlist("core", "simple_auth_manager_users")]
        return [{"username": username, "role": role} for username, role in users]

    def init(self) -> None:
        user_passwords_from_file = {}

        # Read passwords from file
        if os.path.isfile(self.get_generated_password_file()):
            with open(self.get_generated_password_file()) as file:
                passwords_str = file.read().strip()
                user_passwords_from_file = json.loads(passwords_str)

        users = self.get_users()
        usernames = {user["username"] for user in users}
        self.passwords = {
            username: password
            for username, password in user_passwords_from_file.items()
            if username in usernames
        }
        for user in users:
            if user["username"] not in self.passwords:
                # User dot not exist in the file, adding it
                self.passwords[user["username"]] = self._generate_password()

            self._print_output(f"Password for user '{user['username']}': {self.passwords[user['username']]}")

        with open(self.get_generated_password_file(), "w") as file:
            file.write(json.dumps(self.passwords))

    def is_logged_in(self) -> bool:
        return "user" in session or conf.getboolean("core", "simple_auth_manager_all_admins")

    def get_url_login(self, **kwargs) -> str:
        """Return the login page url."""
        return url_for("SimpleAuthManagerAuthenticationViews.login", next=kwargs.get("next_url"))

    def get_url_logout(self) -> str:
        return url_for("SimpleAuthManagerAuthenticationViews.logout")

    def get_user(self) -> SimpleAuthManagerUser | None:
        if not self.is_logged_in():
            return None
        if conf.getboolean("core", "simple_auth_manager_all_admins"):
            return SimpleAuthManagerUser(username="anonymous", role="admin")
        else:
            return session["user"]

    def deserialize_user(self, token: dict[str, Any]) -> SimpleAuthManagerUser:
        return SimpleAuthManagerUser(username=token["username"], role=token["role"])

    def serialize_user(self, user: SimpleAuthManagerUser) -> dict[str, Any]:
        return {"username": user.username, "role": user.role}

    def is_authorized_configuration(
        self,
        *,
        method: ResourceMethod,
        details: ConfigurationDetails | None = None,
        user: SimpleAuthManagerUser | None = None,
    ) -> bool:
        return self._is_authorized(method=method, allow_role=SimpleAuthManagerRole.OP, user=user)

    def is_authorized_connection(
        self,
        *,
        method: ResourceMethod,
        details: ConnectionDetails | None = None,
        user: SimpleAuthManagerUser | None = None,
    ) -> bool:
        return self._is_authorized(method=method, allow_role=SimpleAuthManagerRole.OP, user=user)

    def is_authorized_dag(
        self,
        *,
        method: ResourceMethod,
        access_entity: DagAccessEntity | None = None,
        details: DagDetails | None = None,
        user: SimpleAuthManagerUser | None = None,
    ) -> bool:
        return self._is_authorized(
            method=method,
            allow_get_role=SimpleAuthManagerRole.VIEWER,
            allow_role=SimpleAuthManagerRole.USER,
            user=user,
        )

    def is_authorized_asset(
        self,
        *,
        method: ResourceMethod,
        details: AssetDetails | None = None,
        user: SimpleAuthManagerUser | None = None,
    ) -> bool:
        return self._is_authorized(
            method=method,
            allow_get_role=SimpleAuthManagerRole.VIEWER,
            allow_role=SimpleAuthManagerRole.OP,
            user=user,
        )

    def is_authorized_pool(
        self,
        *,
        method: ResourceMethod,
        details: PoolDetails | None = None,
        user: SimpleAuthManagerUser | None = None,
    ) -> bool:
        return self._is_authorized(
            method=method,
            allow_get_role=SimpleAuthManagerRole.VIEWER,
            allow_role=SimpleAuthManagerRole.OP,
            user=user,
        )

    def is_authorized_variable(
        self,
        *,
        method: ResourceMethod,
        details: VariableDetails | None = None,
        user: SimpleAuthManagerUser | None = None,
    ) -> bool:
        return self._is_authorized(method=method, allow_role=SimpleAuthManagerRole.OP, user=user)

    def is_authorized_view(
        self, *, access_view: AccessView, user: SimpleAuthManagerUser | None = None
    ) -> bool:
        return self._is_authorized(method="GET", allow_role=SimpleAuthManagerRole.VIEWER, user=user)

    def is_authorized_custom_view(
        self, *, method: ResourceMethod | str, resource_name: str, user: SimpleAuthManagerUser | None = None
    ):
        return self._is_authorized(method="GET", allow_role=SimpleAuthManagerRole.VIEWER, user=user)

    def filter_permitted_menu_items(self, menu_items: list[MenuItem]) -> list[MenuItem]:
        return menu_items

    def register_views(self) -> None:
        if not self.appbuilder:
            return
        self.appbuilder.add_view_no_menu(
            SimpleAuthManagerAuthenticationViews(
                users=self.get_users(),
                passwords=self.passwords,
            )
        )

    def _is_authorized(
        self,
        *,
        method: ResourceMethod,
        allow_role: SimpleAuthManagerRole,
        allow_get_role: SimpleAuthManagerRole | None = None,
        user: SimpleAuthManagerUser | None = None,
    ):
        """
        Return whether the user is authorized to access a given resource.

        :param method: the method to perform
        :param allow_role: minimal role giving access to the resource, if the user's role is greater or
            equal than this role, they have access
        :param allow_get_role: minimal role giving access to the resource, if the user's role is greater or
            equal than this role, they have access. If not provided, ``allow_role`` is used
        :param user: the user to check the authorization for. If not provided, the current user is used
        """
        user = user or self.get_user()
        if not user:
            return False

        user_role = user.get_role()
        if not user_role:
            return False

        role_str = user_role.upper()
        role = SimpleAuthManagerRole[role_str]
        if role == SimpleAuthManagerRole.ADMIN:
            return True

        if not allow_get_role:
            allow_get_role = allow_role

        if method == "GET":
            return role.order >= allow_get_role.order
        return role.order >= allow_role.order

    @staticmethod
    def _generate_password() -> str:
        return "".join(random.choices("abcdefghkmnpqrstuvwxyzABCDEFGHKMNPQRSTUVWXYZ23456789", k=16))

    @staticmethod
    def _print_output(output: str):
        name = "Simple auth manager"
        colorized_name = colored(f"{name:10}", "white")
        for line in output.splitlines():
            print(f"{colorized_name} | {line.strip()}")
