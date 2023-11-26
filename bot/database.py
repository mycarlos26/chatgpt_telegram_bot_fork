from typing import Optional, Any

#from pymongo.mongo_client import MongoClient
#from pymongo.server_api import ServerApi
import pymongo
import uuid
from datetime import datetime
import requests
import json
import config
import openai
import aiohttp
import motor.motor_asyncio

# setup openai
openai.api_key = config.openai_api_key
if config.openai_api_base is not None:
    openai.api_base = config.openai_api_base

class Database:
    def __init__(self):
       
        #self.client = pymongo.MongoClient(config.local)
        #self.cliend_asin = motor.motor_asyncio.AsyncIOMotorClient(config.local)
        self.client = pymongo.MongoClient(config.mongodb_uri)
        self.cliend_asin = motor.motor_asyncio.AsyncIOMotorClient(config.mongodb_uri)
        #self.client = MongoClient(config.mongodb_uri_atlas, server_api=ServerApi('1'))
        self.db = self.client["chatgpt_telegram_bot"]
        self.db_asin = self.cliend_asin["chatgpt_telegram_bot"]
        self.thread_id = ""
        self.user_collection = self.db["user"]
        self.user_collection_asin = self.db_asin["user"]
        self.dialog_collection = self.db["dialog"]
        self.dialog_collection_asin = self.db_asin["dialog"]

    def check_if_user_exists(self, user_id: int, raise_exception: bool = False):
        if self.user_collection.count_documents({"_id": user_id}) > 0:
            return True
        else:
            if raise_exception:
                raise ValueError(f"User {user_id} does not exist")
            else:
                return False
    async def check_if_user_exists_asin(self, user_id: int, raise_exception: bool = False):
        if await self.user_collection_asin.count_documents({"_id": user_id}) > 0:
            return True
        else:
            if raise_exception:
                raise ValueError(f"User {user_id} does not exist")
            else:
                return False

    def add_new_user(
        self,
        user_id: int,
        chat_id: int,
        username: str = "",
        first_name: str = "",
        last_name: str = "",
    ):
        if not self.check_if_user_exists(user_id):
            empty_thread_id = Database.create_thread()
            self.thread_id  = empty_thread_id
        user_dict = {
            "_id": user_id,
            "chat_id": chat_id,

            "username": username,
            "first_name": first_name,
            "last_name": last_name,

            "last_interaction": datetime.now(),
            "first_seen": datetime.now(),

            "current_dialog_id": None,
            "current_chat_mode": "assistant",
            "current_model": config.models["available_text_models"][0],
            "thread_id" :empty_thread_id,
            "n_used_tokens": {},

            "n_generated_images": 0,
            "n_transcribed_seconds": 0.0  # voice message transcription
        }

        if not self.check_if_user_exists(user_id):
            self.user_collection.insert_one(user_dict)

    def start_new_dialog(self, user_id: int):
        self.check_if_user_exists(user_id, raise_exception=True)

        dialog_id = str(uuid.uuid4())
        dialog_dict = {
            "_id": dialog_id,
            "user_id": user_id,
            "chat_mode": self.get_user_attribute(user_id, "current_chat_mode"),
            "start_time": datetime.now(),
            "model": self.get_user_attribute(user_id, "current_model"),
            "messages": []
        }

        # add new dialog
        self.dialog_collection.insert_one(dialog_dict)

        # update user's current dialog
        self.user_collection.update_one(
            {"_id": user_id},
            {"$set": {"current_dialog_id": dialog_id}}
        )

        return dialog_id

    def get_user_attribute(self, user_id: int, key: str):
        self.check_if_user_exists(user_id, raise_exception=True)
        user_dict = self.user_collection.find_one({"_id": user_id})

        if key not in user_dict:
            return None

        return user_dict[key]
    
    async def get_user_attribute_asincrona(self, user_id: int, key: str):
        await self.check_if_user_exists_asin(user_id, raise_exception=True)
        user_dict = await self.user_collection_asin.find_one({"_id": user_id})

        if key not in user_dict:
            return None

        return user_dict[key]
    
    def set_user_attribute(self, user_id: int, key: str, value: Any):
        self.check_if_user_exists(user_id, raise_exception=True)
        self.user_collection.update_one({"_id": user_id}, {"$set": {key: value}})

    def update_n_used_tokens(self, user_id: int, model: str, n_input_tokens: int, n_output_tokens: int):
        n_used_tokens_dict = self.get_user_attribute(user_id, "n_used_tokens")

        if model in n_used_tokens_dict:
            n_used_tokens_dict[model]["n_input_tokens"] += n_input_tokens
            n_used_tokens_dict[model]["n_output_tokens"] += n_output_tokens
        else:
            n_used_tokens_dict[model] = {
                "n_input_tokens": n_input_tokens,
                "n_output_tokens": n_output_tokens
            }

        self.set_user_attribute(user_id, "n_used_tokens", n_used_tokens_dict)

    def get_dialog_messages(self, user_id: int, dialog_id: Optional[str] = None):
        self.check_if_user_exists(user_id, raise_exception=True)

        if dialog_id is None:
            dialog_id = self.get_user_attribute(user_id, "current_dialog_id")

        dialog_dict = self.dialog_collection.find_one({"_id": dialog_id, "user_id": user_id})
        return dialog_dict["messages"]

    def set_dialog_messages(self, user_id: int, dialog_messages: list, dialog_id: Optional[str] = None):
        self.check_if_user_exists(user_id, raise_exception=True)

        if dialog_id is None:
            dialog_id = self.get_user_attribute(user_id, "current_dialog_id")

        self.dialog_collection.update_one(
            {"_id": dialog_id, "user_id": user_id},
            {"$set": {"messages": dialog_messages}}
        )

    @staticmethod
    def create_thread():
        url = "https://api.openai.com/v1/threads"       
        # Agrega los encabezados necesarios para tu solicitud, como el token de la API.
        headers = {
            "Authorization": f"Bearer {config.openai_api_key}",
            "Content-Type": "application/json",
            'OpenAI-Beta': 'assistants=v1',
        }       
        # Realiza la petición POST al servidor. En este ejemplo no enviamos datos adicionales (data={}),
        response =  requests.post(url, headers=headers)
        
        # Comprobamos que la respuesta tiene el estatus 200 que implica éxtio
        if response.ok:
            # Convertimos la respuesta en formato JSON a un diccionario de Python
            response_data = response.json()
            # Accedemos al campo 'id' y lo retornamos
            return response_data.get('id')
        else:
            # Manejamos la respuesta en caso de error
            print(f"Error en la solicitud: {response.status_code}")
            return None

    
    @staticmethod
    async def create_message(thread_id, content):
        url = f"https://api.openai.com/v1/threads/{thread_id}/messages"
        
        headers = {
            "Authorization": f"Bearer {config.openai_api_key}",
            "Content-Type": "application/json",
            'OpenAI-Beta': 'assistants=v1',
        }
        
        # La carga útil que se va a enviar con la solicitud, incluyendo "role" y "content".
        payload = {
            "role": "user",
            "content": content
        }
        async with aiohttp.ClientSession() as session:
             async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    response_data = await response.json()
                    return response_data.get('id')
                else:
                    print(f"Error al crear el mensaje: {response.status}")
                    response_text = await response.text()
                    print(f"Detalle del error: {response_text}")
                    return None

    @staticmethod
    async def create_run(thread_id):
        url = f"https://api.openai.com/v1/threads/{thread_id}/runs"
        
        headers = {
            "Authorization": f"Bearer {config.openai_api_key}",
            "Content-Type": "application/json",
            'OpenAI-Beta': 'assistants=v1',
        }
        payload = {
            "assistant_id": config.assistant_id
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    response_data = await response.json()
                    return response_data.get('id')
                else:
                    print(f"Error al crear el run: {response.status}")
                    response_text = await response.text()
                    print(f"Detalle del error: {response_text}")
                    return None
