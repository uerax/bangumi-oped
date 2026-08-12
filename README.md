# bangumi-oped

![Auto Sync OP/ED Data](https://github.com/bangumi-oped/bangumi-oped/actions/workflows/sync-oped.yml/badge.svg)

`bangumi-oped` 是一个开源的动漫番剧片头（OP）与片尾（ED）跳过时间戳数据库。项目以 [Bangumi 番组计划](https://bgm.tv) 的 **Subject ID** 作为主键索引，旨在为各类第三方播放器、媒体服务器插件（如 Jellyfin/Emby/Plex 插件）、浏览器扩展及跳过脚本提供统一、标准化的 OP/ED 时间数据源。

通过项目内置的 GitHub Action，每周会自动关联 `bangumi-data` 并拉取 [AniSkip](https://aniskip.com) 的最新跳过打点数据进行增量同步。

---

## 目录

- [1. 目录架构与文件命名](#1-目录架构与文件命名)
- [2. 时间戳数据格式 (`ID.txt`)](#2-时间戳数据格式-idtxt)
- [3. 特殊情况处理规范](#3-特殊情况处理规范)
  - [3.1 缺失 OP 或 ED 的处理 (`-1` 哨兵值)](#31-缺失-op-或-ed-的处理--1-哨兵值)
  - [3.2 非正片与缺集处理](#32-非正片与缺集处理)
  - [3.3 多季与续作的集数编号规范](#33-多季与续作的集数编号规范)
  - [3.4 行顺序与集数重复的解析规则](#34-行顺序与集数重复的解析规则)
- [4. 自动同步机制 (GitHub Action)](#4-自动同步机制-github-action)
- [5. 第三方接入与解析示例](#5-第三方接入与解析示例)
- [6. 手动贡献数据须知](#6-手动贡献数据须知)

---

## 1. 目录架构与文件命名

每部番剧在仓库中都对应一个以其 Bangumi **Subject ID** 命名的独立文件夹。

```text
<Subject_ID>/
├── <Subject_ID>.txt   # 实际 OP/ED 时间戳数据文件
└── <Anime Title>      # 人工识别用标记文件（内容为空）
```

### 示例
对应 Bangumi 页面地址为 `https://bgm.tv/subject/622206` 的作品：

```text
622206/
├── 622206.txt   # 数据文件
└── ヤニねこ     # 标记空文件（解析程序应忽略此文件）
```

> **番剧名标记文件规则**：
> - **内容为空**：仅文件名用于人工直观识别番剧，**解析程序必须完全忽略此文件**。
> - **命名规范**：优先使用 bgm.tv 页面上的中文名；若无中文名则使用原名。
> - **非法字符处理**：若文件名包含操作系统不允许的字符（如 `:`、`/`、`\`、`?`、`*`、`"`、`<`、`>`、`|`），需统一替换为全角字符（如 `:` → `：`）或删除。

---

## 2. 时间戳数据格式 (`ID.txt`)

`ID.txt` 采用按行存储的纯文本格式，每一行代表一集，各字段使用英文分号 `;` 分隔，固定为 **5 个字段**：

```text
集数;OP起始时间;OP结束时间;ED起始时间;ED结束时间
```

### 字段定义

| 字段 | 类型 | 单位 | 说明 |
|---|---|---|---|
| 集数 | 整数 | - | 本条目内部的集数序号（必须从 `1` 开始） |
| OP起始时间 | 整数 | 秒 | 片头 OP 开始时间点 |
| OP结束时间 | 整数 | 秒 | 片头 OP 结束时间点 |
| ED起始时间 | 整数 | 秒 | 片尾 ED 开始时间点 |
| ED结束时间 | 整数 | 秒 | 片尾 ED 结束时间点 |

### 示例数据

```text
1;137;227;1327;1417
2;173;263;1327;1417
3;139;229;1327;1417
```

* **第 1 集**：OP 范围为 137 秒 ~ 227 秒，ED 范围为 1327 秒 ~ 1417 秒。

---

## 3. 特殊情况处理规范

### 3.1 缺失 OP 或 ED 的处理 (`-1` 哨兵值)

如果某一集没有 OP、没有 ED 或者两者皆无，**禁止留空或省略字段**，必须显式填充 `-1` 作为哨兵占位值。

* **只有 ED，没有 OP**（如无片头特别篇）：
  ```text
  2;-1;-1;2400;2500
  ```
* **只有 OP，没有 ED**（如终章正片替代 ED）：
  ```text
  3;100;2300;-1;-1
  ```
* **既无 OP 也无 ED**：
  ```text
  4;-1;-1;-1;-1
  ```

> **注意**：`0` 是有效的时间戳秒数（片头可能在 0 秒直接开始），因此不能用 `0` 表示缺失。

### 3.2 非正片与缺集处理

若某一集为总集篇、特别篇或暂未收录，**直接跳过该集数即可**，无需用空行占位。

### 3.3 多季与续作的集数编号规范

部分番剧在 bgm.tv 上第二季或续作会建立**独立条目 (新的 Subject ID)**，但在页面上延续第一季的集数显示（例如第二季第 1 集显示为第 13 集）。

**本项目统一规定**：集数必须为**当前 Subject ID 条目内的相对集数，固定从 `1` 开始编号**。

### 3.4 行顺序与集数重复的解析规则

1. **行顺序无关**：解析器应按显式的集数字段读取，不依赖文件的行号或升序排列。
2. **重复集数处理**：若文件中意外出现相同集数，解析器**以第一次出现的行**为准，忽略后续重复行。

---

## 4. 自动同步机制 (GitHub Action)

本项目配置了自动化同步工作流 (`.github/workflows/sync-oped.yml`与 `scripts/sync_oped.py`)：

1. **数据源映射**：自动关联 [bangumi-data](https://github.com/bangumi-data/bangumi-data) CDN 找到 Bangumi Subject ID 对应的 MyAnimeList (MAL) ID。
2. **打点数据源**：调用 [AniSkip API](https://aniskip.com) 拉取最新的 OP/ED 范围。
3. **高效拉取与增量同步**：
   - **已完结番剧**：本地存在数据文件的已完结作品直接跳过，零重复网络请求。
   - **连载中番剧**：在 `.state.json` 中追踪增量集数，每周尝试探测新发行的集数打点。

---

## 5. 第三方接入与解析示例

调用方只需根据 Bangumi Subject ID 读取对应的 `<Subject_ID>/<Subject_ID>.txt` 文件即可。Python 解析示例：

```python
import os

def parse_bangumi_oped(subject_id: int | str, base_dir: str = ".") -> dict:
    folder = os.path.join(base_dir, str(subject_id))
    filepath = os.path.join(folder, f"{subject_id}.txt")
    
    episodes = {}
    if not os.path.exists(filepath):
        return episodes

    with open(filepath, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(";")
            if len(parts) != 5:
                continue
            
            ep, op_s, op_e, ed_s, ed_e = map(int, parts)
            episodes[ep] = {
                "op": None if op_s == -1 else (op_s, op_e),
                "ed": None if ed_s == -1 else (ed_s, ed_e),
            }
    return episodes

# 使用示例
data = parse_bangumi_oped(622206)
print(data.get(1))  # {'op': (3, 93), 'ed': (1417, 1507)}
```

---

## 6. 手动贡献数据须知

欢迎提交 Pull Request 补充或修正数据！提交时请注意：
1. 确保新建文件夹名称与 Bangumi Subject ID 一致。
2. 保持空文件名与作品中文名/原名一致，并用全角字符替换非法路径字符。
3. `ID.txt` 保证 5 字段完整，缺省字段使用 `-1`。
