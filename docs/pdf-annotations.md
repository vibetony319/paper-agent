# PDF 文本批注后端契约

本文说明文本锚点、高亮、笔记和选区辅助接口的服务端契约。前端 PDF.js 采集部分由前端计划补充，但服务端不接受像素坐标。

## 坐标系与选区限制

- 所有坐标都是相对单页的归一化值，范围 `[0, 1]`，必须满足 `0 <= x0 <= x1 <= 1`、`0 <= y0 <= y1 <= 1`。
- 矩形按 `order` 从 0 连续编号，服务端要求 `order == range(len(rects))`。
- 首版只支持单页选区；跨页结构会在写入前返回 `422 validation_error`。
- `quote` 去空白后不能为空，长度不超过 12,000 字符；矩形数量 1 到 200。
- 可选的 `element_id` 必须属于同一论文，页码必须真实存在于该论文。

原文去重哈希为 `sha256(normalize("NFKC", " ".join(quote.split())).casefold())`，用于同一论文内复用已有锚点。

## 前端采集与展示

- 一个阅读器只创建一个 PDF.js 文档加载任务。连续页面共用该任务；每页的 Canvas 与 TextLayer 从同一个 viewport 生成，Canvas 仅提高 backing store 的 DPI，不改变文字层的几何坐标。
- 页面壳使用 `IntersectionObserver` 挂载，`rootMargin` 为 `1200px 0px`；可视页前后页作为轻量预取。未进入范围的页面保留正确长宽比的占位壳，不产生单独的 PDF 加载任务。
- 前端只接受同一页、可复制 TextLayer 内的选择。pointer、键盘选择触发的 `selectionchange` 都会转为原文、页码和多矩形锚点；矩形会相对同页 surface 归一化并裁剪到 `[0, 1]`。跨页、空白或脱离 TextLayer 的选择会被静默清除，不写入服务端。
- 选区工具栏固定在视口内，会在选区、窗口尺寸和可用空间变化时重新夹紧位置；解释、翻译、手写笔记与高亮均复用同一份归一化锚点。缩放或阅读区宽度改变后，Canvas、TextLayer、证据层和高亮层以新的共享 viewport 重新恢复位置。
- 蓝靛色证据定位层与黄色批注高亮层相互独立：证据定位是当前会话的临时阅读辅助，高亮和笔记才是持久批注。高亮重新挂载时按保存的归一化矩形恢复，不依赖旧的浏览器像素坐标。
- `GET /api/papers/{paper_id}/annotations` 响应除 `highlights`、`notes` 外，还可加性返回 `anchors`；前端用它恢复没有独立高亮的手写/生成笔记的摘录与定位。

限制：扫描件、图片中的文字、损坏的 TextLayer 和不可复制 PDF 不能在浏览器中制作文本锚点；目前不提供 OCR 或跨页选区合并。用户仍可从原始 PDF 链接查看此类页面。

## 幂等键

高亮和笔记请求可带 `request_id`。相同论文和请求标识重复提交时返回已保存对象；如果原文、坐标或正文与首次请求不一致，返回 `409 idempotency_conflict`，不会静默复用。选区辅助的幂等状态单独记录在 `selection_assist_requests`。

## 删除关系

- 删除高亮不会删除文本锚点或引用该锚点的笔记。
- 删除笔记不会删除高亮，也不自动删除锚点。
- 消息历史引用的笔记被删除后，历史响应保留 `note_id` 并标记 `available: false`。

论文级永久删除的表清单、删除顺序与恢复语义见[数据模型与删除恢复](data-model.md)。

## API

### 获取批注

```text
GET /api/papers/{paper_id}/annotations
```

响应包含 `highlights` 和 `notes`，并可加性包含 `anchors`；每条批注带原文、页码和矩形，不包含本地文件路径。

### 创建高亮

```bash
curl -X POST http://127.0.0.1:8000/api/papers/<paper-id>/highlights \
  -H "Content-Type: application/json" \
  -d '{
    "quote": "selected text",
    "page_number": 1,
    "rects": [{"order": 0, "x0": 0.1, "y0": 0.2, "x1": 0.8, "y1": 0.25}],
    "color": "yellow",
    "request_id": "1c4b6ab7-3e3c-4c18-8b3e-5f31d9141c45"
  }'
```

### 删除高亮

```text
DELETE /api/papers/{paper_id}/highlights/{highlight_id}
```

### 笔记 CRUD

```text
POST   /api/papers/{paper_id}/notes
PATCH  /api/papers/{paper_id}/notes/{note_id}
DELETE /api/papers/{paper_id}/notes/{note_id}
```

创建请求示例：

```json
{
  "body": "我的笔记",
  "page_number": 1,
  "note_type": "manual",
  "anchor": {
    "quote": "selected text",
    "page_number": 1,
    "rects": [{"order": 0, "x0": 0.1, "y0": 0.2, "x1": 0.8, "y1": 0.25}]
  },
  "request_id": "8c8d1d6c-7e1c-4a5b-9f6a-19bb2314a02b"
}
```

客户端只能写 `note_type: "manual"`。`PATCH` 只接受正文和 `expected_updated_at`，乐观锁冲突返回 `409 idempotency_conflict`；成功编辑会把 `user_edited` 置为真。

### 选区解释与翻译

```text
POST /api/papers/{paper_id}/selection-assists
```

请求在选区字段基础上增加 `action`、`model_profile_id` 和 `request_id`。响应是 `text/event-stream`，事件固定为 `started`、`delta`、`completed`、`error`。只有 `completed` 才表示正式笔记已创建；取消、模型失败或输出超过 64,000 字符都只记录失败，不产生残缺笔记。

## 服务端校验

领域对象在持久化前再次验证页码、元素归属和坐标顺序；Pydantic 负责范围与长度，服务层负责跨论文归属和幂等冲突。所有用户可见错误使用 `{code, detail}` 中文信息，不返回模型或数据库原始异常。
