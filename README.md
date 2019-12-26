<p align="center">
    <h2 align="center">Telethon X Fire - TeleFire</h2>
</p>

<p align="center">A set of userful command line tools to interact with telegram.</p>

<p align="center">
    <b><a href="#what-has-inside">What has inside</a></b>
    |
    <b><a href="#how">How</a></b>
    |
    <b><a href="#docker">Docker</a></b>
</p>


## What has inside

- :flags:<a href="#get_all_chats">get_all_chats</a>: Fetches all the chat IDs and names.
- :bookmark_tabs:<a href="#list_messages">list_messages</a>: List messages in a certain chat.
- :mag:<a href="#search_messages">search_messages</a>: Search messages in a certain chat.
- :skull:<a href="#delete_all">delete_all</a>: Delete all the messages that you have permission to delete in a certain chat.
- :bomb:<a href="#auto_delete">auto_delete</a>: Delete certain messages after certain time.
- :speech_balloon:<a href="#words_to_ifttt">words_to_ifttt</a>: Send an event to IFTTT when somebody said some words
  you interested.

## How

### Setup

0. [Login to your Telegram account](https://my.telegram.org/auth) with the phone number of the account you wish to use.
1. Click **API Development tools**.
2. A `Create new application` window will appear if you didn't create one. Go head and create one.
3. Once you finish creation, get the `api_id` and `api_hash`, you will use it later.


### get_all_chats

```shell
python telefire.py --api_id=[YOUR_API_ID] --api_hash=[YOUR_API_HASH] get_all_chats

-100XXXXXXXXXX: CHANNEL_NAME0
    XXXXXXXXXX: CHANNEL_NAME1
```
Those negative IDs start with `-100` are private groups, that's the only way you can access to these groups. For public groups, you can either use id, public url, username to access to it.


### list_messages

```shell
python telefire.py --api_id=[YOUR_API_ID] --api_hash=[YOUR_API_HASH] list_messages --chat [CHAT_IDENTIFIER] [Optional: --user USER_IDENTIFIER]
```
For `CHAT_IDENTIFIER`, it can be a chat ID you got from <a href="#get_all_chats">get_all_chats</a>, or it can be something like `t.me/LGTMer` or `LGTMer`.

For `USER_IDENTIFIER`, it can be the user's ID or username.


### search_messages

```shell
python telefire.py --api_id=[YOUR_API_ID] --api_hash=[YOUR_API_HASH] search_messages --peer [PEER_IDENTIFIER] --query [QUERY_STRING]
```
This command comes with some optional parameters that you can custom:
- `--slow`: Whether to use telegram's search API or iterate through whole message history to do the search. The later can be comprehensive if you are searching UTF-8 characters such as Chinases.
- `--limit [INTEGER]`: Set the limit of search result, default `100`.
- `--from_id [USER_IDENTIFIER]`: The id/username of the message sender.

### delete_all

```shell
python telefire.py --api_id=[YOUR_API_ID] --api_hash=[YOUR_API_HASH] delete_all --chat [CHAT_IDENTIFIER] [Optional: --query QUERY_STRING]
```
For `CHAT_IDENTIFIER`, smiliar to `CHAT_IDENTIFIER` in <a href="#get_all_chats">get_all_chats</a>, or it can be something like `t.me/LGTMer` or `LGTMer`.

You can also using the `--query` to specify only messages containing certain string will be deleted.

### auto_delete

```shell
python telefire.py --api_id=[YOUR_API_ID] --api_hash=[YOUR_API_HASH] auto_delete
```
It's a command you have to keep it running in the backgroud to use it. Then you can send any messages in your app start with something like `10s `(notice the space) and then followed by your original messages you want to send, this message will be deleted automately in 10 seconds. you can also specify  minutes(`m`), hours(`h`) and days(`d`) as the message experation time.

### words_to_ifttt

```shell
python telefire.py --api_id=[YOUR_API_ID] --api_hash=[YOUR_API_HASH] words_to_ifttt --event [IFTTT EVENT] --key [IFTTT WEBHOOK KEY] [WORDS YOU INTERESTED]
```

Like `auto_delete`, you need to keep this command running to make it work. For the `event` and `key`, you can get it from [here](https://ifttt.com/maker_webhooks). For `WORDS YOU INTERESTED`, it can be something like `telefire "telefire is so cool"`, then whenever anybody said **telefire** or **telefire is so cool**, an IFTTT event will be sent and you can create an applet to do whatever you like on IFTTT, such as sending notifications, turn on a light, etc.



## Docker

This project also come with a `Dockerfile` so that you don't need to setup any python environment, just run the following command:
```shell
docker build . -t telefire
docker run -ti --rm -v $(pwd)/telefire.py:/tg/telefire.py telefire python telefire.py [COMMAND] [OPTIONS]
```
And that's it, enjoy!

## TODO

- :heavy_check_mark: For deleting messages, add an option to delete messages based on time instead of always delete all.
- :heavy_check_mark: A long-running service that will notify user if someone said something contains some interested words.
