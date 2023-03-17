import os
from typing import Dict, List
import time
import openai
from collections import deque
from datetime import datetime
from crawler import Crawler
import prompt as p

openai.api_key = os.environ["OPENAI_API_KEY"]
# consts:
hist_len = 5
quote_buffer_limit = 500


def buffer2string(buffer: Dict) -> str:
    # create string representation of buffer
    # merge consequtive text nodes on the same tree lvl
    out = ""
    bk = list(buffer.keys())
    i = 0

    while i < len(bk):
        if buffer[bk[i]]["node_type"] == "text":
            text_node_id = bk[i]
            local_text_buffer = buffer[bk[i]]["inner_text"]
            j = i + 1

            while True:
                if j >= len(bk):
                    break

                if buffer[bk[j]]["node_type"] == "text":
                    local_text_buffer += " " + buffer[bk[j]]["inner_text"]
                    j += 1
                    continue
                elif buffer[bk[j]]["node_type"] == "sep":
                    if local_text_buffer:
                        out += f"\n<text>{local_text_buffer}</text>"
                    out += "\n"
                    j += 1
                    break
                else:
                    if local_text_buffer:
                        out += f"\n<text>{local_text_buffer}</text>"
                        local_text_buffer = ""
                    out += "\n" + buffer[bk[j]]["meta"]
                    j += 1
                    continue
            i = j
        else:
            if buffer[bk[i]]["node_type"] != "sep":
                out += "\n" + buffer[bk[i]]["meta"]
            i += 1
    return out.strip()


def quote_buffer_to_string(quote_buffer: List[Dict[str, str]]) -> str:
    out = ""
    for i, q_d in enumerate(quote_buffer):
        out += f"QUOTE {i}\nPAGE TITLE: {q_d['page_title']}\nURL: {q_d['domain'][:150]}\nANSWER:\n{q_d['extract']}\n"
    return out


def quote_buffer_to_short_string(quote_buffer: List[Dict[str, str]]) -> str:
    out = ""
    for i, q_d in enumerate(quote_buffer):
        out += f"{q_d['page_title'][:33]+'...'} | {q_d['domain'][:60]+'...'}\n{q_d['extract'][:100]+'...'}\n"
    return out


def get_gpt_instruction(
    objective: str,
    url: str,
    command_history: deque,
    quote_buffer: List,
    browser_content: str,
) -> str:
    previous_commands = "\n".join(command_history)
    quotes_summary = quote_buffer_to_short_string(quote_buffer) if quote_buffer else ""

    prompt = p.retrieval_prompt.format(
        objective=objective,
        url=url[:120],
        previous_commands=previous_commands,
        browser_content=browser_content[:3600],
        quotes=quotes_summary,
    )
    response = openai.Completion.create(
        model="text-davinci-003",
        prompt=prompt,
        temperature=0.75,
        best_of=4,
        n=2,
        max_tokens=550,
    )
    # print(response)
    return response.choices[0].text


def get_gpt_answer(objective: str, quote_buffer: List[Dict[str, str]]) -> str:
    quote_str = quote_buffer_to_string(quote_buffer)
    prompt = p.answering_prompt.format(question=objective, quotes=quote_str)
    response = openai.Completion.create(
        model="text-davinci-003",
        prompt=prompt,
        temperature=0.7,  # higher the more creative
        best_of=3,
        n=2,
        max_tokens=1000,
    )
    return response.choices[0].text


def get_gpt_welcome_msg(current_time: str, user_name: str = "Eric") -> str:
    prompt = p.welcome_prompt.format(current_time=current_time, user_name=user_name)
    response = openai.Completion.create(
        model="text-davinci-003",
        prompt=prompt,
        temperature=0.7,
        max_tokens=256,
        top_p=1,
        frequency_penalty=0,
        presence_penalty=0,
    )
    return response.choices[0].text


if __name__ == "__main__":
    _c = Crawler(limit_to_viewport=True, viewport_height=750)

    def instruct(ins: str) -> bool:
        ins = ins.split("\n")[0]

        if ins.startswith("SCROLL UP"):
            _c.scroll("up")
            return True
        elif ins.startswith("SCROLL DOWN"):
            _c.scroll("down")
            return True
        elif ins.startswith("CLICK: "):
            # CLICK 1
            _id = ins.split(" ")[1]
            _c.click(_id)
            return True
        elif ins.startswith("TYPE ") or ins.startswith("SUBMIT "):
            # TYPE 1 "Paris City Centre"
            # TYPESUBMIT 1 "Paris City Centre"
            space_separated = ins.split(" ")
            _id = space_separated[1][:-1]
            text = " ".join(space_separated[2:])
            # if text[0] == '"' and text[-1] == '"':
            #     text = text[1:-1]
            _c.type(_id, text)
            if not text:
                print(f" > Passed empty string to TYPE command")
                return
            if space_separated[0] == "SUBMIT":
                _c.enter()
            return True
        elif ins.startswith("SELECT "):
            # SELECT ID "value"
            space_separated = ins.split(" ")
            _id = space_separated[1][:-1]
            value = " ".join(space_separated[2:])
            # if value[0] == '"' and value[-1] == '"':
            #     value = value[1:-1]
            _c.select(_id, value)
            return True
        elif ins.startswith("QUOTE: "):
            space_separated = ins.split(" ")
            quote = " ".join(space_separated[1:])
            to_append = {
                "page_title": _c.page.title(),
                "domain": _c.page.url,
                "extract": quote,
            }
            quote_buffer.append(to_append)
            print(f" > memorized: {quote}")
            return True
        elif ins == "BACK":
            _c.back()
            return True
        elif ins.startswith("ANSWER"):
            print(" > Switching to answering mode...")
            return True
        else:
            print(f" > Command: `{ins}` is not recognized")
            return False

    objective = """Why did we decide that certain words were "bad" and shouldn't be used in social settings?"""
    current_time_str = datetime.today().strftime("%I:%M %p")
    print(get_gpt_welcome_msg(current_time_str))
    i = input("\n")
    if i:
        objective = i
    else:
        print(objective, "\n")

    gpt_ins = ""
    history = deque(maxlen=hist_len)
    quote_buffer = []

    _c.go_to_page("https://www.google.com/")

    try:
        while True:
            buffer = _c.parse()
            browser_content = buffer2string(buffer)

            gpt_ins = get_gpt_instruction(
                objective, _c.page.url, history, quote_buffer, browser_content
            )
            gpt_ins = gpt_ins.strip()

            quote_buffer_str = quote_buffer_to_string(quote_buffer)
            # print("Objective:" + objective)
            # print("Quotes:", quote_buffer_str)
            # print("Past actions:", history)

            # print("URL:" + _c.page.url)
            # print("----------------\n" + browser_content + "\n----------------\n")

            print(" > issued instruction:\n" + gpt_ins + "\n")

            # usr_cmd = input("Press ENTER to accept:")
            # if not usr_cmd:
                # usr_cmd = gpt_ins
            usr_cmd = gpt_ins

            status = instruct(usr_cmd)
            if status:
                history.append(usr_cmd)
                if usr_cmd == "ANSWER" or len(quote_buffer_str) >= quote_buffer_limit:
                    ans = get_gpt_answer(objective, quote_buffer)
                    print(f"\nANSWER:\n{ans}\n")
                    input()
                else:
                    time.sleep(4.0)

    except KeyboardInterrupt:
        print("\nBye!")
        exit(0)