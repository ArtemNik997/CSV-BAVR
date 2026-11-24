import json
from unittest.mock import Mock, patch

import pytest
import requests

from impressum import (
    ApiError,
    extract_with_deepseek,
    _content_from_body,
    _strip_code_fences,
    _normalize_fields,
    ADDRESS_FIELDS,
    RESPONSE_FIELDS,
    RETRY_DELAYS,
    DEEPSEEK_URL,
    SYSTEM_PROMPT,
)


def make_response(content, status_code=200):
    """Create a mocked successful DeepSeek API response."""
    response = Mock()
    response.status_code = status_code
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": content}}]}
    return response


VALID_JSON = json.dumps(
    {
        "company_name": "ACME GmbH",
        "postal_address": {
            "street": "Main Street",
            "house_no": "12",
            "postcode": "10115",
            "city": "Berlin",
            "country": "Germany",
        },
        "handelsregister": "HRB 12345",
        "vat_number": "DE123456789",
        "phone": "+49 30 123456",
        "email": "INFO@ACME.DE",
        "responsible_person": "Max Mustermann",
    }
)


@patch("impressum.time.sleep")
@patch("impressum.requests.post")
def test_successful_extraction(mock_post, mock_sleep, monkeypatch):
    """Valid JSON is parsed and normalized."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-api-key")
    mock_post.return_value = make_response(VALID_JSON)

    result = extract_with_deepseek("Impressum text")

    assert result["company_name"] == "ACME GmbH"
    assert result["postal_address"]["city"] == "Berlin"
    assert result["vat_number"] == "DE123456789"
    assert result["email"] == "INFO@ACME.DE"

    mock_post.assert_called_once()
    mock_sleep.assert_not_called()


@patch("impressum.time.sleep")
@patch("impressum.requests.post")
def test_retries_after_empty_response(mock_post, mock_sleep, monkeypatch):
    """An empty response is retried."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-api-key")

    mock_post.side_effect = [
        make_response(""),
        make_response(VALID_JSON),
    ]

    result = extract_with_deepseek("Impressum text")

    assert result["company_name"] == "ACME GmbH"
    assert mock_post.call_count == 2
    mock_sleep.assert_called_once_with(2)


@patch("impressum.time.sleep")
@patch("impressum.requests.post")
def test_retries_after_invalid_json(mock_post, mock_sleep, monkeypatch):
    """Invalid JSON is retried."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-api-key")

    mock_post.side_effect = [
        make_response("This is not JSON"),
        make_response(VALID_JSON),
    ]

    result = extract_with_deepseek("Impressum text")

    assert result["company_name"] == "ACME GmbH"
    assert mock_post.call_count == 2
    mock_sleep.assert_called_once_with(2)


@patch("impressum.time.sleep")
@patch("impressum.requests.post")
def test_returns_empty_dict_after_retries(mock_post, mock_sleep, monkeypatch):
    """Repeated invalid responses result in an empty dictionary."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-api-key")

    mock_post.return_value = make_response("")

    result = extract_with_deepseek("Impressum text")

    assert result == {}
    assert mock_post.call_count == 4
    assert [call.args[0] for call in mock_sleep.call_args_list] == [2, 4, 8]


@patch("impressum.time.sleep")
@patch("impressum.requests.post")
def test_401_raises_api_error_without_retry(mock_post, mock_sleep, monkeypatch):
    """HTTP 401 is not retried."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-api-key")

    response = Mock()
    response.status_code = 401
    response.raise_for_status.side_effect = requests.HTTPError("401 Unauthorized")
    mock_post.return_value = response

    with pytest.raises(ApiError):
        extract_with_deepseek("Impressum text")

    mock_post.assert_called_once()
    mock_sleep.assert_not_called()


@patch("impressum.requests.post")
def test_missing_api_key_raises_error(mock_post, monkeypatch):
    """A missing API key raises ApiError."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(ApiError):
        extract_with_deepseek("Impressum text")

    mock_post.assert_not_called()


@patch("impressum.requests.post")
def test_missing_fields_are_normalized(mock_post, monkeypatch):
    """Missing JSON fields are filled with None."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-api-key")
    mock_post.return_value = make_response('{"company_name": "ACME GmbH"}')

    result = extract_with_deepseek("Impressum text")

    assert result["company_name"] == "ACME GmbH"
    assert result["vat_number"] is None
    assert result["email"] is None
    assert result["postal_address"] == {
        "street": None,
        "house_no": None,
        "postcode": None,
        "city": None,
        "country": None,
    }


@patch("impressum.requests.post")
def test_markdown_json_fences_are_supported(mock_post, monkeypatch):
    """Optional Markdown JSON fences are removed."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-api-key")
    mock_post.return_value = make_response("```json\n" + VALID_JSON + "\n```")

    result = extract_with_deepseek("Impressum text")

    assert result["company_name"] == "ACME GmbH"


@patch("impressum.requests.post")
def test_request_uses_expected_parameters(mock_post, monkeypatch):
    """The API request contains the expected model and JSON settings."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-api-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")
    mock_post.return_value = make_response(VALID_JSON)

    extract_with_deepseek("Example Impressum")

    args, kwargs = mock_post.call_args

    assert args[0] == "https://api.deepseek.com/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer test-api-key"
    assert kwargs["json"]["model"] == "deepseek-chat"
    assert kwargs["json"]["response_format"] == {"type": "json_object"}
    assert kwargs["json"]["temperature"] == 0
    assert kwargs["json"]["max_tokens"] == 1024
    assert kwargs["timeout"] == 60
    assert "Example Impressum" in kwargs["json"]["messages"][1]["content"]


# ---------------------------------------------------------- _strip_code_fences


@pytest.mark.parametrize(
    "source, expected",
    [
        ('{"a":1}', '{"a":1}'),
        ('```json\n{"a":1}\n```', '{"a":1}'),
        ('```\n{"a":1}\n```', '{"a":1}'),
        ('```JSON\n{"a":1}\n```', '{"a":1}'),
        ('```Json\n{"a":1}\n```', '{"a":1}'),
        ('  {"a":1}  ', '  {"a":1}  '),  # без ограждений не трогаем
        ("", ""),
    ],
)
def test_code_fences_are_stripped(source, expected):
    assert _strip_code_fences(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        '```json\n{"a":1}',  # обрезанный ответ, нет закрывающего
        '```json\n{"a":1}\n```\nHope this helps!',  # комментарий после ограждения
    ],
)
def test_broken_fences_stay_unparseable(source):
    """Такой ответ должен уйти на повтор, а не «починиться» до валидного JSON."""
    with pytest.raises(ValueError):
        json.loads(_strip_code_fences(source))


# -------------------------------------------------------- _content_from_body


def test_content_is_stripped():
    body = {"choices": [{"message": {"content": "  hi \n"}}]}
    assert _content_from_body(body) == "hi"


def test_first_choice_is_used():
    body = {"choices": [{"message": {"content": "a"}}, {"message": {"content": "b"}}]}
    assert _content_from_body(body) == "a"


@pytest.mark.parametrize(
    "body",
    [
        [],  # не dict
        {},  # нет choices
        {"choices": []},  # пустой список
        {"choices": "x"},  # не список
        {"choices": [1]},  # элемент не dict
        {"choices": [{}]},  # нет message
        {"choices": [{"message": None}]},
        {"choices": [{"message": {"content": None}}]},
        {"choices": [{"message": {"content": 5}}]},  # не строка
        {"choices": [{"message": {"content": "  \n "}}]},
        {"error": {"message": "Model Not Exist"}},  # ошибка API вместо ответа
        "string",
        42,
        None,
    ],
)
def test_unusable_body_raises_value_error(body):
    """ValueError, а не KeyError/IndexError — иначе исход станет статусом 4 вместо 2."""
    with pytest.raises(ValueError):
        _content_from_body(body)


# --------------------------------------------------------- _normalize_fields


def test_missing_top_level_keys_become_none():
    result = _normalize_fields({"company_name": "A"})
    assert all(result[f] is None for f in RESPONSE_FIELDS if f != "company_name")


def test_address_skeleton_is_always_complete():
    result = _normalize_fields({})
    assert set(result["postal_address"]) == set(ADDRESS_FIELDS)
    assert all(v is None for v in result["postal_address"].values())


@pytest.mark.parametrize("address", ["Berlin", None, 42, [], ["a"]])
def test_non_dict_address_becomes_empty(address):
    result = _normalize_fields({"company_name": "A", "postal_address": address})
    assert result["postal_address"] == {f: None for f in ADDRESS_FIELDS}


def test_unknown_address_subfield_is_dropped_known_is_kept():
    result = _normalize_fields({"postal_address": {"city": "Berlin", "zip": "999"}})
    assert "zip" not in result["postal_address"]
    assert result["postal_address"]["city"] == "Berlin"


def test_normalize_does_not_mutate_the_source():
    source = {"company_name": "A", "fax": "1"}
    result = _normalize_fields(source)
    result["_raw"] = source
    assert source == {"company_name": "A", "fax": "1"}


def test_email_case_is_left_to_stage_9():
    """lower() делает map_to_row, клиент отдаёт как есть."""
    assert _normalize_fields({"email": "INFO@ACME.DE"})["email"] == "INFO@ACME.DE"


def http_error_response(status):
    resp = Mock()
    resp.status_code = status
    resp.raise_for_status.side_effect = requests.HTTPError(str(status))
    return resp


@patch("impressum.time.sleep")
@patch("impressum.requests.post")
def test_empty_then_http_error_still_returns_empty_dict(
    mock_post, mock_sleep, monkeypatch
):
    """Пустой ответ важнее поздней сетевой ошибки: статус 2, не 4 (решение №3)."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    mock_post.side_effect = [
        make_response(""),
        make_response(""),
        make_response(""),
        http_error_response(500),
    ]
    assert extract_with_deepseek("text") == {}


@patch("impressum.time.sleep")
@patch("impressum.requests.post")
def test_mixed_transport_and_content_is_deterministic(
    mock_post, mock_sleep, monkeypatch
):
    """Чередование 500/пустой ответ не меняет исход."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    mock_post.side_effect = [
        http_error_response(500),
        make_response(""),
        http_error_response(500),
        make_response(""),
    ]
    assert extract_with_deepseek("text") == {}


@patch("impressum.time.sleep")
@patch("impressum.requests.post")
def test_all_http_errors_raise_api_error(mock_post, mock_sleep, monkeypatch):
    """Ни одного валидного HTTP-ответа -> ApiError (статус 4)."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    mock_post.return_value = http_error_response(503)
    with pytest.raises(ApiError):
        extract_with_deepseek("text")
    assert mock_post.call_count == 4
    assert [c.args[0] for c in mock_sleep.call_args_list] == [2, 4, 8]


@patch("impressum.time.sleep")
@patch("impressum.requests.post")
def test_html_body_is_retried_then_empty_dict(mock_post, mock_sleep, monkeypatch):
    """requests.JSONDecodeError — наследник и RequestException, и ValueError."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    resp = Mock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.side_effect = requests.exceptions.JSONDecodeError(
        "Expecting value", "<html>", 0
    )
    mock_post.return_value = resp
    assert extract_with_deepseek("text") == {}
    assert mock_post.call_count == 4


@patch("impressum.time.sleep")
@patch("impressum.requests.post")
def test_body_without_choices_returns_empty_dict(mock_post, mock_sleep, monkeypatch):
    """Ошибка API в теле с HTTP 200 — это парсинг, а не транспорт."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    resp = Mock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"error": {"message": "Model Not Exist"}}
    mock_post.return_value = resp
    assert extract_with_deepseek("text") == {}


@patch("impressum.time.sleep")
@patch("impressum.requests.post")
def test_json_array_is_not_an_object(mock_post, mock_sleep, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    mock_post.return_value = make_response("[1, 2, 3]")
    assert extract_with_deepseek("text") == {}


@pytest.mark.parametrize("status", [401, 403])
@patch("impressum.time.sleep")
@patch("impressum.requests.post")
def test_auth_errors_are_not_retried(mock_post, mock_sleep, status, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    resp = Mock()
    resp.status_code = status
    mock_post.return_value = resp
    with pytest.raises(ApiError, match="API key"):
        extract_with_deepseek("text")
    mock_post.assert_called_once()
    mock_sleep.assert_not_called()


@patch("impressum.time.sleep")
@patch("impressum.requests.post")
def test_429_is_retried(mock_post, mock_sleep, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    mock_post.side_effect = [http_error_response(429), make_response(VALID_JSON)]
    assert extract_with_deepseek("text")["company_name"] == "ACME GmbH"
    mock_sleep.assert_called_once_with(2)


@pytest.mark.parametrize("text", ["", "   ", "\n\t "])
@patch("impressum.requests.post")
def test_blank_text_skips_the_api(mock_post, text, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    assert extract_with_deepseek(text) == {}
    mock_post.assert_not_called()


@patch("impressum.time.sleep")
@patch("impressum.requests.post")
def test_api_key_never_appears_in_error_text(mock_post, mock_sleep, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-secret-123")
    mock_post.side_effect = requests.ConnectionError("boom")
    with pytest.raises(ApiError) as exc:
        extract_with_deepseek("text")
    assert "sk-secret-123" not in str(exc.value)


@patch("impressum.requests.post")
def test_raw_keeps_the_full_parsed_object(mock_post, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    payload = {"company_name": "ACME GmbH", "fax": "+49 30 999", "extra": {"a": 1}}
    mock_post.return_value = make_response(json.dumps(payload))

    result = extract_with_deepseek("text")

    assert result["_raw"] == payload
    assert "fax" not in result
    assert "extra" not in result
    assert result["company_name"] == "ACME GmbH"


@patch("impressum.requests.post")
def test_raw_of_empty_object_is_empty(mock_post, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    mock_post.return_value = make_response("{}")
    result = extract_with_deepseek("text")
    assert result["_raw"] == {}
    assert all(result[f] is None for f in RESPONSE_FIELDS)


def test_prompt_and_constants_match_the_task():
    assert "json" in SYSTEM_PROMPT.lower()
    assert '"postal_address"' in SYSTEM_PROMPT
    assert '"handelsregister"' in SYSTEM_PROMPT
    assert DEEPSEEK_URL == "https://api.deepseek.com/chat/completions"
    assert RETRY_DELAYS == (2, 4, 8)


@patch("impressum.requests.post")
def test_system_prompt_is_sent_verbatim(mock_post, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    mock_post.return_value = make_response(VALID_JSON)
    extract_with_deepseek("text")
    messages = mock_post.call_args.kwargs["json"]["messages"]
    assert messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert messages[1]["content"].startswith(
        "Extract the Impressum data from the text below and return JSON:"
    )


@patch("impressum.requests.post")
def test_model_env_override_is_honoured(mock_post, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-reasoner")
    mock_post.return_value = make_response(VALID_JSON)
    extract_with_deepseek("text")
    assert mock_post.call_args.kwargs["json"]["model"] == "deepseek-reasoner"
