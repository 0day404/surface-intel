# 组件→检查要点清单

用途：FOFA/curl 指纹命中已知组件时，先对这张表再写风险点——CVE 线索不凭记忆。
条目格式：**指纹线索** → **检查要点**（CVE/端点）→ **判定方式**。
铁律：本表所有判定都是**非侵入**的（版本对照/端点存在性）；利用与 PoC 发送超出 safe_probe 边界，只记录线索不验证利用。新组件命中即追加本表。

## 国际设备/软件

| 组件 | 指纹线索 | 检查要点 | 判定方式 |
|---|---|---|---|
| Citrix NetScaler ADC/Gateway | 响应头 `Server: NS*`；title "Citrix Gateway" | CVE-2023-4966（Citrix Bleed，会话令牌泄露）；CVE-2023-3519（未认证 RCE，看版本）；CVE-2025-5777（SAML 令牌泄露） | 登录页版本号对照修补区间；`/oauth/idp/.well-known/openid-configuration` 可达性（4966 IOC） |
| F5 BIG-IP | `Server: BigIP`；`/tmui/login.jsp` | CVE-2020-5902（TMUI RCE）；CVE-2022-1388（iControl REST 认证绕过）；CVE-2023-46747（管理接口绕过 RCE） | `/tmui/` 路径可达性 + 登录页版本 |
| Ivanti Connect Secure / Pulse Secure | `/dana-na/` 路径族 | CVE-2023-46805 + CVE-2024-21887（认证绕过链 RCE）；CVE-2024-21893（SSRF）；CVE-2025-0282（栈溢出） | 版本页对照；`/dana-na/auth/url_default/welcome.cgi` 存在性 |
| VMware Horizon | title "VMware Horizon"；`/portal/webclient` | CVE-2021-44228（Log4Shell 路径）；CVE-2023-20887（RCE） | 版本对照修补区间 |
| Mitel MiCollab | title "MiCollab"；`/portal/` | CVE-2024-41713（NUID 参数处理缺陷→任意文件读取）；后续同族 CVE 随实战补 | `/portal/` 版本信息 |
| Kerio Control | `Server: Kerio_Control`；管理口 4081 | CVE-2024-52875（认证前 SSRF→RCE 链） | 版本 banner；`/nonauth/` 端点存在性（只记存在性） |
| Zimbra Collaboration | `Set-Cookie: ZM_TEST=true`；`/zimbra/` | CVE-2024-45519（postjournal RCE）；CVE-2022-41352（附件 RCE）；CVE-2023-37580（XSS 链） | 版本 + postjournal 相关端点存在性 |
| Jenkins | 响应头 `X-Jenkins` | CVE-2024-23897（CLI 任意文件读）；CVE-2018-1000861（元编程 RCE） | 版本头；`/script` 控制台暴露本身记 P3 |
| GitLab | 页脚 "GitLab"；`/users/sign_in` | CVE-2023-7028（密码重置接管）；CVE-2021-22205（ExifTool RCE）；CVE-2022-2185（RCE） | `/api/v4/version` 公开可读时取版本 |
| Atlassian Confluence | title "Confluence"；`/login.action` | CVE-2023-22527（OGNL RCE）；CVE-2023-22518（重置）；CVE-2023-22515（权限绕过建管理员） | `/status` 返回版本 JSON |
| MOVEit Transfer | title "MOVEit"；`/human.aspx` | CVE-2023-34362（SQLi→RCE）；CVE-2023-35036 | 页面版本号 |
| Progress WS_FTP | title "WS_FTP"；管理口 5466 | CVE-2023-40044（反序列化 RCE） | 版本 banner |
| Sophos Firewall | `/userportal/` | CVE-2022-1040（SRTP 认证绕过 RCE）；CVE-2020-12271（SSL VPN RCE） | 版本页 |
| SonicWall SMA/GMS | `/cgi-bin/`；SMA 登录页 | SMA 100：CVE-2021-20016（SQLi）；GMS：CVE-2023-34262（未认证 RCE） | 版本对照 |
| Palo Alto GlobalProtect | `/global-protect/login` | CVE-2024-3400（任意文件读→RCE） | 版本对照修补区间 |

## 国内设备/OA（编号以实际核查为准，未核实的标"待补号"）

| 组件 | 指纹线索 | 检查要点 | 判定方式 |
|---|---|---|---|
| 用友 NC | `/servlet/~ic/bsh.servlet.BshServlet` 类路径 | BshServlet 反序列化（待补号）；弱口令默认账密 | 端点存在性；title "UFIDA"/"用友" |
| 泛微 e-cology / e-office | `/weaver/` 路径族 | BshServlet 反序列化；e-office SQLi（待补号） | title "e-cology"/版本页 |
| 通达 OA | title "通达 OA"；`/logincheck.php` | 历史 getshell 系列（待补号） | 版本页 |
| 蓝凌 OA | title "蓝凌"；`/dataos/` | SSRF→RCE（CNVD-2021-01346） | 版本页 |
| 深信服 EDR/SSL VPN | `/portal/` 登录页；EDR 控制台 | EDR RCE（CNVD-2020-46552）；VPN 口令逻辑缺陷 | 版本 + 控制台暴露即记风险 |
| 大华/海康 摄像头/NVR | title "WEB SERVICE"；`/doc/page/login.asp` | 大华认证绕过（CVE-2021-33044/33045）；海康命令注入（CVE-2021-36260） | 版本；暴露面本身即风险 |

## 通用检查动作（指纹命中后按序）

1. curl 拿全量返回包（`curl -sS -i`），提取版本 banner 对照上表修补区间。
2. 管理面/登录入口暴露 → 直接进 xlsx 风险表 P3（若无版本缺陷线索）/P4，"管理面"列=是。
3. 版本落在已知 CVE 修补区间之前 → 风险表升级 P2，CVE 号写"CVE 线索"列，状态="待核实利用面"。
4. 端点存在性判定只做 GET/HEAD，不发利用 payload——safe_probe 边界。
