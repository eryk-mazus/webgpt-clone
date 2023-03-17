# webgpt-clone
browser-assisted question-answering

## setup

* install requirements:
```
pip install -r requirements.txt

playwright install
```

* add openai api key to the environment variables:
```
set OPENAI_API_KEY=XXX
```

* run
```
python ./src/webgpt.py
```

## known bugs:
- occasional indexerror in `buffer2string` function 
- trimming the parsed website content in `get_gpt_instruction` function will sometimes cut the parts responsible for accepting cookies 
- it is possible to overflow the gpt's max sequence length right now
