# slack.py

import json
import secrets
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse
import httpx
import asyncio
import base64
import requests
from integrations.integration_item import IntegrationItem
import urllib.parse

from redis_client import add_key_value_redis, get_value_redis, delete_key_redis

CLIENT_ID = '249d546f-188a-414a-89d9-51086ae9121a'
CLIENT_SECRET = '6572f06b-2c8a-43b4-905f-4cacb7d44bff'
REDIRECT_URI = 'http://localhost:8000/integrations/hubspot/oauth2callback'
scope = 'crm.objects.contacts.read crm.objects.contacts.write crm.objects.deals.read'

encodeURIComponent_CLIENT_ID = urllib.parse.quote(CLIENT_ID, safe='()*!\'')
encodeURIComponent_scope = urllib.parse.quote(scope, safe='()*!\'')


authorization_url = f'https://app.hubspot.com/oauth/authorize?client_id={encodeURIComponent_CLIENT_ID}&redirect_uri={REDIRECT_URI}&scope={encodeURIComponent_scope}'


async def authorize_hubspot(user_id, org_id):
    print('authorize_hubspot called')
    state_data = {
        'state': secrets.token_urlsafe(32),
        'user_id': user_id,
        'org_id': org_id
    }
    encoded_state = json.dumps(state_data)
    await add_key_value_redis(f'hubspot_state:{org_id}:{user_id}', encoded_state, expire=600)

    return f'{authorization_url}&state={encoded_state}'

async def oauth2callback_hubspot(request: Request):
    print('oauth2callback_hubspot called')
    if request.query_params.get('error'):
        raise HTTPException(status_code=400, detail=request.query_params.get('error'))
    code = request.query_params.get('code')
    encoded_state = request.query_params.get('state')
    state_data = json.loads(encoded_state)

    original_state = state_data.get('state')
    user_id = state_data.get('user_id')
    org_id = state_data.get('org_id')

    saved_state = await get_value_redis(f'hubspot_state:{org_id}:{user_id}')

    if not saved_state or original_state != json.loads(saved_state).get('state'):
        raise HTTPException(status_code=400, detail='State does not match.')
    
    async with httpx.AsyncClient() as client:
        response, _ = await asyncio.gather(
            client.post(
                'https://api.hubspot.com/oauth/v1/token',
                data={
                    'grant_type': 'authorization_code',
                    'client_id': CLIENT_ID,
                    'client_secret': CLIENT_SECRET,
                    'redirect_uri': REDIRECT_URI,
                    'code': code
                }
            ),
            delete_key_redis(f'hubspot_state:{org_id}:{user_id}')
        )
    
    print(response.json())
    await add_key_value_redis(f'hubspot_credentials:{org_id}:{user_id}', json.dumps(response.json()), expire=600)

    close_window_script = """
    <html>
        <script>
            window.close();
        </script>
    </html>
    """
    return HTMLResponse(content=close_window_script)

async def get_hubspot_credentials(user_id, org_id):
    print('get_hubspot_credentials called')
    credentials = await get_value_redis(f'hubspot_credentials:{org_id}:{user_id}')
    if not credentials:
        raise HTTPException(status_code=400, detail='No credentials found.')
    credentials = json.loads(credentials)
    if not credentials:
        raise HTTPException(status_code=400, detail='No credentials found.')
    await delete_key_redis(f'hubspot_credentials:{org_id}:{user_id}')

    return credentials

async def create_integration_item_metadata_object(response_json: dict) -> IntegrationItem:
    print('create_integration_item_metadata_object called')
    return IntegrationItem(
            id=response_json['id'],
            name=response_json['properties']['firstname'],
            type='contact',
            creation_time=response_json['createdAt'],
            last_modified_time=response_json['updatedAt']
        )

async def get_items_hubspot(credentials):
    print('get_items_hubspot called')
    credentials = json.loads(credentials)
    access_token = credentials.get('access_token')
    print("access_token: ", access_token)
    print("--------------------------------------------------------------------------------------------------------------")
    response = requests.get(
        'https://api.hubspot.com/crm/v3/objects/contacts', 
        headers={
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
    )

    list_of_integration_item_metadata = []
    if response.status_code == 200:
        results = response.json()['results']
        print(results)
        for result in results:
            integration_item_metadata = await create_integration_item_metadata_object(result)
            list_of_integration_item_metadata.append(integration_item_metadata)
    return list_of_integration_item_metadata