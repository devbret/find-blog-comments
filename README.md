# Find Blog Comments

A web-crawling utility which automatically identifies and logs external links containing active comment sections or third-party discussion platforms by scanning a target website's internal pages.

## Application Overview

A web crawler designed to identify external websites featuring active comment sections. It begins by crawling a specified root URL and exploring internal pages within the same domain up to a specified limit. During this initial phase, the script extracts all unique external links found on those internal pages while incorporating delays and timeouts to help ensure polite network requests.

Once the list of external links is compiled, the script analyzes each one specifically for evidence of comment functionality. It uses a layered detection strategy to check for known third-party commenting platforms, specific HTML attributes and common phrases. If an external page is determined to have a comment section based on these indicators, its URL is recorded and saved to a local text file.

## Basic Setup Instructions

Below are the required software programs and instructions for installing and using this application on a Linux machine.

### Programs Needed

- [Git](https://git-scm.com/downloads)

- [Python](https://www.python.org/downloads/)

### Steps For Use

1. Install the above programs

2. Open a terminal

3. Clone this repository: `git clone git@github.com:devbret/find-blog-comments.git`

4. Navigate to the repo's directory: `cd find-blog-comments`

5. Create a virtual environment: `python3 -m venv venv`

6. Activate your virtual environment: `source venv/bin/activate`

7. Install the needed dependencies: `pip install -r requirements.txt`

8. Run the program: `python3 app.py https://example.com`

9. Exit the virtual environment: `deactivate`

## Other Considerations

This project repo is intended to demonstrate an ability to do the following:

- Crawl a specified website's internal pages to identify and collect unique external links

- Analyze gathered external links using multiple indicators to detect active comment sections

- Look for evidence of third-party commenting tools like Disqus, Commento and others

- Generate a text file containing the URLs of all external pages that were confirmed to have comments

If you have any questions or would like to collaborate, please reach out either on GitHub or via [my website](https://bretbernhoft.com/).
