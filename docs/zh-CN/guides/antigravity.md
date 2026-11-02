# 复用本地 agy 登录接入 Antigravity

支持 **Windows 和带会话 D-Bus／Secret Service 的 Linux 本机 CLI＋Web、单账号**。先通过官方 `agy` 登录。
KT 不实现 OAuth 登录，不保存 refresh token，也不会替用户退出 Google 账号。

```bash
kt login google-antigravity
kt config antigravity status
kt config antigravity refresh
kt config antigravity models
kt run ./my-creature --llm google-antigravity/gemini-4-argon@reasoning=medium
```

- `status` 仅查看本地凭据状态，不联网。
- `refresh` 在必要时运行 `agy --output-format json models`，由 agy 自己续期。
- `models` 联网获取账号可用模型，不产生推理。
- Web 设置的「OAuth 登录」区块中，Google Antigravity 仅提供「检查状态」，遵循管理员权限配置。
- 已过期且 agy 可续期的凭据显示「待自动刷新」，下次请求时自动尝试续期；Web 不提供手动续期按钮。
- 提供者页不展示服务端模型列表；预置模型与自定义模型统一在「自定义模型」页管理。

## 内置模型与 reasoning effort

预设对齐 agy 1.4.12 CLI 的 agent 模型列表（2026-11-02）。服务端按客户端
User-Agent 决定返回哪份列表，KT 发送与 agy 相同的 `antigravity/cli/<version> (...)`
格式；其他格式只会拿到较小的目录，目录外的模型返回 404。以下均为 token 限制，不是账号额度。

| `google-antigravity/` 下的预设 | 上下文选项 | 输出 | reasoning 档位 | 发送的 thinking |
| --- | --- | ---: | --- | --- |
| `gemini-4-argon` | 256k（默认）、512k、900k | 65,536 | low、medium、high | `thinkingLevel` |
| `gemini-3.8-flash` | 256k（默认）、1048k | 65,536 | low、medium、high | `thinkingBudget` 1,000 / 4,000 / 动态 |
| `claude-opus-5-5` | 256k（默认）、840k | 128,000 | low、medium、high | `thinkingLevel` |
| `claude-sonnet-5-5` | 256k（默认）、840k | 128,000 | low、medium、high | `thinkingLevel` |
| `gpt-oss-120b` | 80k（默认）、131k | 32,768 | 仅 medium | `thinkingBudget` 8,192 |

每个档位是独立的实际模型 `<预设>-<档位>`（如 `gemini-4-argon-high`），并发送目录中
对应的 thinking 设置。默认档位与 agy 相同为 **medium**。可用 `@reasoning=low|medium|high`
或 CLI/Web 模型选择器切换；也接受 `claude-opus-5-5-low` 等明确 ID，ID 与 effort 冲突会在
读取凭据前报错。Gemini 4 Argon 需要 Google AI Ultra（或符合条件的 Enterprise）账号。
目录标注 GPT-OSS 将于 2026-11-02 下线。

`context` variation 设置 profile 的 `max_context`，即 KT 压缩历史所依据的预算。默认值是
agy 自身的压缩上限；更大的选项分别来自 agy 的 Argon 上下文选择（512k / 900k）、
agy 的 Claude 长上下文上限（840k）以及目录最大值（Flash 1048k、GPT-OSS 131k）。
该选项不发送给服务端；窗口越大，每轮请求携带的输入越多，额度消耗也随之增加。
两个 variation 可以组合，例如
`google-antigravity/gemini-4-argon@reasoning=medium,context=512k`。

已退役的预设会解析到后继预设并记录警告：`gemini-3.6-flash`、`gemini-3.7-flash`、
`gemini-3-flash` → `gemini-3.8-flash`；`gemini-3.1-pro` → `gemini-4-argon`；
`claude-sonnet-4-6` → `claude-sonnet-5-5`；`claude-opus-4-6-thinking` → `claude-opus-5-5`。
内联配置里已退役的实际 ID（`gemini-3.7-flash-tiered`、`gemini-pro-agent`、
`gemini-3.1-pro-high` 等）会在读取凭据前报 `deprecated_model, use <后继>`。
profile 输出上限可手动调低，但不得超过模型上限或小于等于正数 thinking budget。
仍可填写自定义模型 ID，未知模型不会猜测 effort 支持情况。

## 凭据与会话

Windows 仅检查凭据项 `gemini:antigravity`；Linux 精确查询 login collection
中的 `service=gemini`、`username=antigravity`，login collection 不存在时查询 default alias。
Linux 需要安装 `antigravity` extra；读取有 5 秒 D-Bus 等待上限，不创建钥匙环，也不弹出解锁窗口。
两平台同时检查已知 agy 后备文件 `~/.gemini/antigravity-cli/antigravity-oauth-token`。
若两处同时存在凭据，或钥匙环中匹配到多个条目，则明确报错，请先在 agy 中解决来源冲突。
钥匙环锁定或不可用时直接报错，不绕过它选择后备文件；本次不支持仅使用文件的无 D-Bus Linux。
只接受 consumer Bearer 凭据；access token 仅驻留内存，不进入日志。
KT 通过有超时限制的 agy 子进程刷新，跨进程锁及进程内共享任务避免重复刷新。

请求固定发往 `daily-cloudcode-pa.googleapis.com`，使用 agy 1.4.12
请求头配置，其中 `os_type`、`arch` 随宿主平台（`windows`／`linux`，`amd64`／`arm64`）设置，不跟随重定向。项目发现与凭据代次绑定；账号切换期间会重新核验。
收到任何文本、思考、工具调用或签名后，不自动重发该推理。

会话保存原始签名片段，同时绑定实际模型 ID、项目与当前消息内容。
Claude 回传时会组装流式文本／思考块及末尾签名，去掉空占位；Gemini 保留原始片段。
同模型的工具往返、事件回放与恢复会话已通过离线完整 agent 工作流验证。
编辑消息后旧片段失效；跨模型可以保留普通文本，无法安全复用的工具签名历史
会提示新建或压缩会话。改变 effort 会改变实际模型 ID，带工具签名的会话应先压缩或新建；
对应同一实际 ID 的系列名与明确档位 ID 可相互复用历史。切换到 OpenAI 时不会外发 Google 内部状态字段。

## 当前边界

暂不支持远程节点、多账号、macOS 凭据源、无可用 Secret Service 的 Linux、自定义 API 地址、媒体生成及
任意 extra_body 覆盖。支持内联图片，
其他不支持的内容或工具 schema 会明确报错。发现列表不保证每个模型支持所有模态。

该可选接入仍受账号服务条款与限制约束，由使用者决定是否启用。

## Linux 实机测试准备

自动化测试使用假凭据和离线 HTTP 响应，不需要 Google 账号。
实机验证需要准备：

1. 在运行 KT 的 Python 环境中安装本分支：`python -m pip install -e '.[antigravity]'`。
2. 安装官方 agy，并完成个人 Google 账号的 consumer 登录。确认新终端中
   `agy --version`、`agy --output-format json models` 均可正常运行。
3. agy 与 KT 使用相同 Linux 用户、HOME 和会话 D-Bus。
   环境中有 `DBUS_SESSION_BUS_ADDRESS`，并且 GNOME Keyring 或兼容的 KWallet 配置
   正在提供 `org.freedesktop.secrets`。在桌面中预先解锁钥匙环。
4. 不要让旧后备凭据文件与钥匙环登录同时存在，否则 KT 返回 `ambiguous_sources`。

用以下命令检查，不会输出 token：

```bash
busctl --user call org.freedesktop.DBus /org/freedesktop/DBus \
  org.freedesktop.DBus NameHasOwner s org.freedesktop.secrets
kt config antigravity status
kt config antigravity models
```

第一条应返回 `b true`；状态应为 `ready`、来源 `linux_secret_service`，
且 `refresh_available` 为 `true`。`status` 不联网；`models` 验证认证访问，不生成文本。
再通过本文开头的 `kt run` 命令让你的 creature 回复一句话，即可验证生成，会消耗账号额度。
Web 设置应显示相同的本机账号状态和额度；Web 服务也必须继承相同用户、HOME、D-Bus 环境。

续期验证：等待 access token 自然过期，先确认状态为 `expired` 且
`refresh_available: true`，再运行 `kt config antigravity refresh`，预期回到 `ready`。
token 未过期时该命令会跳过 agy，不能证明续期成功。
负面检查可以临时取消 KT 的 `DBUS_SESSION_BUS_ADDRESS`，预期
`credential_store_unavailable`；或在桌面中锁定钥匙环，预期 `credential_store_locked`。
恢复环境／解锁后再检查，应恢复正常。

单次 `max_tokens` 覆盖（包括压缩摘要）不修改已保存的 profile，保持该次总输出上限。
若正数思考预算无法容纳，则仅该次请求取输出上限的一半，最低 128；动态预算原样发送；
输出上限不足以超过最低预算时明确拒绝。
常规请求仍使用配置的 effort 预算，未知生成参数继续报错。
工具结果会回传服务端提供的函数调用 ID；上游未提供 ID 时，KT 生成的 ID 只用于
内部配对，不作为服务端 ID 外发。
