# midas-nx-mcp 사용 설명서

Claude Desktop이나 Codex 같은 AI 앱에서 **지금 열려 있는 MIDAS Gen NX / Civil NX 모델**을
말로 묻고, 필요하면 조심스럽게 고칠 수 있게 해 주는 로컬 MCP 서버입니다.

> MIDAS IT 공식 제품이 아닌 개인 프로젝트입니다. 작은 테스트 모델에서 검증했고(알파),
> 대형 실무 모델에서는 아직 충분히 써 보지 않았습니다. 중요한 모델은 **사본으로** 시험하세요.

---

## 1. 동작 방식

```
AI 앱 (Claude Desktop / Codex / Claude Code)
   │  같은 PC 안에서 stdio로 통신
   ▼
midas-nx-mcp (이 서버, uvx가 실행)
   │  MIDAS NX Open API (MAPI 키)
   ▼
Gen NX / Civil NX (같은 PC에서 실행 중)
```

- 서버는 **AI 앱이 직접 켜고 끕니다.** 따로 실행할 필요가 없습니다.
- AI는 서버가 제공하는 **13개 도구**로만 NX에 접근합니다. 정해진 안전 규칙은 서버가 강제합니다.

---

## 2. 준비물

| 항목 | 내용 |
| --- | --- |
| OS | Windows (NX가 **같은 PC**에서 실행 중이어야 함) |
| NX | Gen NX 또는 Civil NX, Open API 연결 상태 (Apps > API Settings) |
| MAPI 키 | 제품별 키. Apps > API Settings에서 확인. **Gen 키와 Civil 키는 다릅니다** |
| uv | Python과 서버를 자동으로 받아 실행하는 도구 |

uv 설치 (PowerShell에서 한 번):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

설치 후 터미널을 새로 열고 `uvx --version`이 나오면 됩니다.

> **키를 바꿔 넣지 마세요.** 다른 제품의 키를 넣어도 "연결됨"으로 보이지만 실제 작업은 실패합니다.

---

## 3. 설치 (AI 앱에 등록)

### 3-1. Claude Desktop

1. 설정 > 개발자(Developer) > **구성 편집(Edit Config)** 을 누릅니다.
   ("사용자 지정 커넥터 추가"가 아닙니다. 그건 원격 서버용입니다.)
2. 열린 `claude_desktop_config.json`에 아래를 넣습니다. 이미 다른 서버가 있으면
   `mcpServers` 안에 `"midas-nx"` 항목만 추가합니다.

```json
{
  "mcpServers": {
    "midas-nx": {
      "command": "uvx",
      "args": ["midas-nx-mcp"],
      "env": {
        "MIDAS_MAPI_KEY_GEN": "Gen NX 키",
        "MIDAS_MAPI_KEY_CIVIL": "Civil NX 키"
      }
    }
  }
}
```

3. Claude Desktop을 **완전히 종료**(트레이 아이콘까지)한 뒤 다시 실행합니다.
4. 설정 > 개발자에서 `midas-nx`가 **running**이면 성공입니다.

한 제품만 쓰면 그 제품의 키만 넣어도 됩니다.

### 3-2. Codex

설정의 MCP 서버 추가 화면에서 STDIO 방식으로:

- 명령: `uvx`
- 인자: `midas-nx-mcp@latest` (`@latest`를 붙이면 새 버전이 나왔을 때 캐시된 옛 버전 대신 새 버전을 받습니다)
- 환경 변수: `MIDAS_MAPI_KEY_GEN`, `MIDAS_MAPI_KEY_CIVIL`

설정 파일(`~/.codex/config.toml`)로 넣는다면:

```toml
[mcp_servers.midas-nx]
command = "uvx"
args = ["midas-nx-mcp@latest"]
env = { MIDAS_MAPI_KEY_GEN = "Gen NX 키", MIDAS_MAPI_KEY_CIVIL = "Civil NX 키" }
```

### 3-3. Claude Code

```bash
claude mcp add midas-nx -e MIDAS_MAPI_KEY_GEN=Gen키 -e MIDAS_MAPI_KEY_CIVIL=Civil키 -- uvx midas-nx-mcp
```

---

## 4. 처음 확인하기

NX에서 모델을 연 다음 AI에게:

> "Gen NX 연결 확인해줘"

연결됨과 프로그램 이름이 나오면 준비 끝입니다. 이어서:

> "모델 요약해줘"

절점·요소·재료·단면·두께·하중 케이스 개수와 단위계가 나옵니다.

**제품은 항상 말해 주세요.** "Gen", "Civil"을 문장에 넣으면 AI가 맞는 키로 접속합니다.

---

## 5. 이렇게 물어보세요

### 모델 읽기 (모델은 바뀌지 않음)

| 하고 싶은 것 | 예시 |
| --- | --- |
| 개요 | "Civil 모델 요약해줘" |
| 테이블 찾기 | "Gen에서 하중 관련 테이블 뭐 있어?" |
| 테이블 구조 | "Gen의 SECT 테이블은 어떤 필드가 있어?" |
| 기록 읽기 | "절점 1~10번 좌표 보여줘", "사용 중인 단면 목록" |
| 프로젝트 정보 | "프로젝트 상태 보여줘", "단면 특성값 보여줘" |
| 층 검토 파라미터 (Gen만) | "층 검토 파라미터 보여줘" |
| 현재 선택 | "지금 NX에서 선택한 거 뭐야?" |
| 설계 설정 | "설계 기준 뭐로 되어 있어?" |
| 화면 캡처 | "모델 화면 보여줘" (NX 창이 그림으로 옴) |

### 해석 결과 (해석 후)

| 하고 싶은 것 | 예시 |
| --- | --- |
| 결과 테이블 종류 | "볼 수 있는 결과 테이블 목록" |
| 반력 | "DL 반력 보여줘" |
| 변위 | "DL, LL 변위 보여줘" |
| 부재력 | "보 부재력 DL 기준으로" |

해석이 안 된 상태면 NX의 "no analysis result" 메시지가 그대로 전달됩니다.

결과를 바탕으로 판단도 시킬 수 있습니다. 예: "반력 합이 자중과 맞는지 확인해줘",
"지점이 빠진 곳 없는지 봐줘".

### 많은 행

한 번에 최대 200행(`MIDAS_MAX_ROWS`)까지 옵니다. 나머지는 개수만 알려 줍니다.
"다음 200개", "1001~1100번만"처럼 범위를 좁혀 달라고 하면 됩니다.

---

## 6. 모델 바꾸기: 미리보기 → 확인 → 적용

모델이나 파일을 바꾸는 도구는 4개입니다.

| 도구 | 하는 일 |
| --- | --- |
| `run_analysis` | 해석 실행 |
| `save_copy` | `C:\temp`에 자동 이름으로 다른 이름 저장 |
| `write_records` | 기록 생성(create) 또는 수정(update), 한 번에 50개까지 |
| `delete_records` | id를 지정해 기록 삭제, 한 번에 50개까지 |

**모든 변경은 두 단계입니다.**

1. AI가 먼저 **미리보기**를 호출합니다. 아무것도 바뀌지 않고, 무엇이 어떻게 바뀔지와
   확인 토큰이 나옵니다.
2. AI가 미리보기를 보여 주면 **사용자가 "진행해"라고 답합니다.**
3. 같은 토큰으로 다시 호출해야 적용됩니다. 그 사이에 모델이 바뀌었다면 토큰이 맞지 않아 거부됩니다.
4. 적용한 뒤에는 테이블을 다시 읽어 실제로 추가·변경·삭제된 내용을 보고합니다.

예시 대화:

> **사용자:** "Gen에 (4, 3, 0) 위치에 절점 하나 추가해줘"
>
> **AI:** 미리보기 — NODE 13 생성 예정, 해석 결과가 지워진다는 경고 … 진행할까요?
>
> **사용자:** "진행해"
>
> **AI:** NODE 13 추가됨 (다시 읽어서 확인)

### 꼭 알아 둘 것

- **변경을 하면 해석 결과가 지워집니다.** 같은 값을 다시 넣어도 마찬가지입니다. 결과를 다시
  보려면 해석부터 다시 돌리세요.
- **해석 전에 NX가 현재 파일에 덮어 저장합니다**(`.bak` 생성). 원본을 지키려면 먼저 `save_copy`로
  사본을 만든 뒤 해석하세요.
- **한 번도 저장하지 않은 새 문서는 해석이 거부됩니다.** NX가 막히는 "다른 이름으로 저장" 창을
  띄우기 때문입니다. "사본 저장해줘"로 먼저 파일을 만드세요.
- **`save_copy` 후에는 NX가 사본을 열고 작업합니다.** 이후 변경은 사본에 들어가고, 원본은 마지막으로
  저장한 상태 그대로입니다. 저장 위치는 `C:\temp\mcp-save-gen-날짜-시간-xxxxxx.mgbx`
  (Civil은 `.mcbz`) 형식입니다.
- **update는 없는 id를 만들지 않습니다.** 새 기록은 create로 만들어야 합니다. 예외는 설계 단일
  기록(DCO, DCTL, LLRF, LMRR, SRDF)의 1번으로, 처음 PUT할 때 기록이 생깁니다.
- 일부 테이블은 NX가 id를 다시 매깁니다. 보고에 "renumbered"가 나오면 그 id를 기준으로 보세요.
- 사용 중인 절점(요소가 쓰는 절점)은 삭제가 거부됩니다.
- 설계 테이블(DCTL 등)의 일부는 기준 사이에 **하나의 기록을 공유**합니다. RC에서 바꾸면 강재
  쪽에서도 바뀐 값이 보입니다. 삭제하면 지워지지 않고 **초기값으로 리셋**되기도 합니다.

---

## 7. NX에 팝업이 뜨면

**API 호출 도중에 NX가 띄운** 대화상자는 그 호출을 멈춰 세웁니다. 사용자가 직접 연 창
(재료 추가, 다른 이름으로 저장 등)은 읽기를 막지 않습니다(2026-11-03 Gen에서 실측).

- 해석이나 저장 중에 새 창이 뜨면 서버가 바로 알아채고 창 제목과 함께
  `waiting_for_nx_dialog`를 보고합니다. (예: Gen의 "Check Changed Story Information")
- 이때는 **NX 창으로 가서 직접 답하세요.** AI에게 다시 실행시키지 마세요. 중복 실행이 됩니다.
- 일반 호출은 20초 안에 응답이 없으면 타임아웃으로 끊고 "NX 창을 확인하라"고 안내합니다.
- 해석은 기본 300초, 저장은 120초까지 기다립니다. 이 시간을 넘기면 "아직 실행 중일 수 있으니
  다시 실행하지 말라"고 안내합니다.

---

## 8. 하지 않는 것

다음 기능은 위험해서 어떤 단계에서도 도구로 열지 않습니다.

- 새 문서 / 문서 열기 / 가져오기(Import): 열린 모델을 버리게 되고, 대형 모델에서 NX가 멈춘
  적이 있습니다.
- 테이블 전체 삭제, 모델 전체 삭제
- 설계 검토 실행(`*-ANAL`), 강재 코드 체크: 대화상자로 세션이 묶이거나 프로그램이 종료된 이력이 있습니다.
- `C:\temp`(또는 지정 폴더) 밖으로 저장하거나, 사용자가 정한 파일 이름으로 저장하기

`C:\temp`에 있는 `sms_*`, `sf_*`, `gen_rc_temp_*` 파일은 다른 프로그램이 쓰는 파일이므로 건드리지 않습니다.

---

## 9. 설정 (환경 변수)

AI 앱 설정의 `env`에 넣습니다. 키 외에는 모두 생략해도 됩니다.

| 변수 | 기본값 | 의미 |
| --- | --- | --- |
| `MIDAS_MAPI_KEY_GEN` | - | Gen NX 키 |
| `MIDAS_MAPI_KEY_CIVIL` | - | Civil NX 키 |
| `MIDAS_TIMEOUT` | `20` | 일반 호출의 대기 시간(초). 팝업을 빨리 알아채려고 일부러 짧게 둡니다 |
| `MIDAS_ANALYSIS_TIMEOUT` | `300` | 해석 대기 시간(초). 큰 모델이면 늘리세요 |
| `MIDAS_SAVE_TIMEOUT` | `120` | 저장 대기 시간(초) |
| `MIDAS_MAX_ROWS` | `200` | 한 번에 돌려주는 최대 행 수 |
| `MIDAS_OUTPUT_DIR` | `C:/temp` | 캡처·사본을 저장할 폴더. **미리 만들어 두어야 합니다** |

---

## 10. 문제 해결

| 증상 | 확인할 것 |
| --- | --- |
| 앱에 `midas-nx`가 안 보이거나 failed | 앱을 트레이까지 완전히 종료했는지, JSON 쉼표·괄호, `uvx --version`이 되는지 |
| `uvx` 실행 실패 / "unsatisfiable" | 새 버전 배포 직후면 몇 분 뒤 다시 시도. `uv cache clean` 후 재시작 |
| 새 버전이 반영되지 않음 | 인자를 `midas-nx-mcp@latest`로 바꾸거나 `uv cache clean` |
| "MIDAS_MAPI_KEY_... is not set" | 그 제품의 키를 `env`에 넣었는지 |
| 연결은 되는데 읽기가 실패 | Gen/Civil 키가 바뀌어 들어가지 않았는지, NX에서 API가 연결 상태인지 |
| 갑자기 모든 호출이 타임아웃 | NX에 팝업이 떠 있는지 확인하고 직접 닫기 |
| 결과 테이블이 "no analysis result" | 해석을 하지 않았거나, 이후 변경으로 결과가 지워짐 |
| 해석이 거부됨 | 한 번도 저장하지 않은 문서이거나, NX 창이 이 PC에서 보이지 않음 → 먼저 사본 저장 |
| 캡처·저장이 실패 | `MIDAS_OUTPUT_DIR` 폴더가 있는지 (없는 경로는 NX에 막히는 창을 띄움) |

---

## 11. 도구 목록 (참고)

AI가 알아서 고르므로 이름을 외울 필요는 없습니다.

| 도구 | 종류 | 내용 |
| --- | --- | --- |
| `check_connection` | 읽기 | 연결 여부, 프로그램 이름 |
| `list_tables` | 읽기 | `/db`, `/DESIGN` 테이블 목록 (검색 가능) |
| `describe_table` | 읽기 | NX가 알려 주는 테이블 필드 구조 (`/db`만) |
| `read_table` | 읽기 | 테이블 기록 (id 지정 가능) |
| `model_summary` | 읽기 | 주요 테이블 개수, 단위계 |
| `read_model_info` | 읽기 | 프로젝트 상태, 단면 특성, 층 검토 파라미터(Gen), 현재 선택 |
| `list_result_tables` | 읽기 | 결과 테이블 종류 |
| `result_table` | 읽기 | 결과 테이블 (하중 케이스, 성분, 대상 지정) |
| `capture_view` | 읽기 | NX 화면 PNG (최대 2000px) |
| `run_analysis` | 변경 | 해석 실행 |
| `save_copy` | 변경 | 사본 저장 |
| `write_records` | 변경 | 기록 생성·수정 |
| `delete_records` | 변경 | 기록 삭제 |

---

- 소스와 이슈: https://github.com/Dennis5882/midas-nx-mcp
- PyPI: https://pypi.org/project/midas-nx-mcp/
- 기반 SDK: https://github.com/Dennis5882/MIDAS-API-NX-SDK
