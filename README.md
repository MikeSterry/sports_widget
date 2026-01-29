## Sports Widget
### Description
Python app to create sports widgets that can be used in webpages. My exact use was to create something that could be imbedded within my personal https://gethomepage.dev page. 

![GetHomePage Example](screenshots/GetHomePageExample.png)

![Widget Example](screenshots/WidgetExample.png)

### Running
- I wrote this to work as a docker stack and I included a run.sh script for slightly better ease of use
- You can set a variety of defaults in the docker compose
	- TEAM_CODE: The abbreviated name of your desired NHL team
	- DEFAULT_DIVISION: Name of the NHL division you'd like to capture for your widget
	- CACHE_TTL_SECONDS: Number of seconds to cache results for upcoming and recent games
	- STANDINGS_CACHE_TTL_SECONDS: Number of seconds to cache results for standings
	- LIMIT_UPCOMING: The number of upcoming game results you want
	- LIMIT_RECENT: The number of recent game results you want

### Endpoints
- /health
	- Generic health endpoint that can be used for your docker stack
- /api
	- Returns a json body of the results
- /widget/hockey
	- Returns a widget
- /widget/hockey/upcoming
	- Returns a widget with just the upcoming games
- /widget/hockey/recent
	- Returns a widget with just the recent and live games
- /widget/hockey/standings
	- Returns a widget with just the standings for your desired division

### Endpoint Query Parameters
There are a few query parameters you can add to the end of each endpoint. 
Note: Parameters are only available with endpoints that would return the desired result. "standings" won't work with the "upcoming" or "recent" endpoints
- theme
	- Set the theme
- upcoming
	- Set the number of upcoming game results
- recent
	- Set the number of recent(and live) game results
- standings
	- Whether standings should be included
	- 1 or 0
- division
	- Override the division

### Widget Themes
There are 3 themes to pick from
- light
- dark
- transparent

### Endpoint example
Say you want a transparent theme, 5 upcoming games, 5 recent games, Central division and show the standings. Your url would look like this
`/widget/hockey?theme=transparent&upcoming=5&recent=5&division=central&standings=1`

Say you want just the standings with a transparent theme?
`/widget/hockey/standings?theme=transparent`

