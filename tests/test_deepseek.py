import json
from unittest.mock import Mock, patch

import pytest
import requests

from impressum import ApiError, extract_with_deepseek


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
