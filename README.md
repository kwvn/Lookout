![Lookout](.github/banner.gif)

## Lookout

A teen safety bot for Discord servers where teens and adults hang out together.

It checks replies and mentions from adults to teens and removes the ones that cross the line, plus any contact info sent to teens. Everything else is left alone. Mods get private reports and one place to handle them.

Servers that use Lookout also warn each other through the Lookout Network. If Lookout's filter catches someone in 2 servers, with 2 different teens, other servers' mods get a heads-up when that person shows up. Mods can't add anyone to it.

Support server: https://discord.gg/BKedYyHgn9

## Running

I'd rather you didn't run your own instance. Just invite the bot. The source is here so server owners can see what it does with their messages.

If you want to anyway:

1. **Get Python 3.12**

   Older versions might work, but I haven't tried them.

2. **Set up a venv**

   `py -3.12 -m venv venv`

3. **Install dependencies**

   `venv\Scripts\python -m pip install -r requirements.txt`

4. **Configuration**

   Copy `.env.example` to `.env` and put your token in it. Everything else in there is optional.

   The filter patterns aren't public, so people can't read exactly what gets caught. Without a `wordlists.py` the bot falls back to the few in `wordlists_example.py`, which is enough to see how it works.

5. **Developer Portal**

   Turn on the **Message Content** intent. **User Install** is only needed if you want DM reports.

6. **Run it**

   `venv\Scripts\python bot.py`

   It prints an invite link with the right permissions. After that, run `/setup` in your server and drag Lookout's role above your members.

It doesn't need Administrator and it can't ban, kick or manage roles.

## Requirements

- Python 3.12+
- discord.py 2.7
- python-dotenv
- Pillow
- NumPy
- NudeNet

## Privacy

Records are deleted after 90 days, warning signs after 30 and Lookout Network records after a year. Removing the bot deletes that server's data, except its Lookout Network records.

[Privacy Policy](https://mammoth-elbow-750.notion.site/Lookout-Privacy-Policy-cbc29be557c84c9085bf55ac9e90e351) · [Terms of Service](https://mammoth-elbow-750.notion.site/Lookout-Terms-of-Service-811dfe2233704bf780b9433ddee23df3)

Inter font by Rasmus Andersson (OFL). Image detection by [NudeNet](https://github.com/notAI-tech/NudeNet).

## License

MPL-2.0, see [LICENSE.txt](LICENSE.txt).
