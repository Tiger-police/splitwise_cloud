# 切分策略计算模块对接简版说明

本文档用于给“切分策略计算模块”确认当前云端后端的真实对接协议。

当前对接方式是：

1. 云端后端主动请求算法服务计算策略
2. 算法服务先快速返回“已受理”
3. 算法服务异步回调云端后端，提交最终切分策略

当前**没有**在请求体里下发 `callback_url`。  
默认由算法服务侧固定配置云端后端回调地址。

---

## 1. 云端后端 -> 算法服务

### 请求地址

当前由后端配置项控制：

```text
ALGORITHM_API_URL
```

默认值：

```text
http://10.144.144.2:5000/api/calculate
```

说明：

- `ALGORITHM_API_URL` 需要根据算法计算模块的**真实服务地址**进行修改
- 上面这个 `http://10.144.144.2:5000/api/calculate` 在当前项目里是 **mock_algorithm_server.py** 的模拟地址
- 如果切换到真实算法服务，请将其改成真实可达地址

### 请求方法

```http
POST
```

### 请求头

```http
Content-Type: application/json
```

### 请求体格式

```json
{
  "task_id": "87534955-99cb-4eed-8232-3dbfcd83010a",
  "model_type": "llama-3.2-3b",
  "state_vector": [
    0.01,
    0.02,
    0.03,
    0.04,
    0.05,
    0.06,
    0.07,
    0.08,
    0.09,
    0.10,
    0.11,
    0.12,
    0.13,
    0.14,
    0.15,
    0.16,
    0.17,
    0.18,
    0.19,
    0.20,
    0.21,
    0.22,
    0.23,
    0.24,
    0.25,
    0.26
  ]
}
```

### 字段说明

- `task_id`
  - 字符串
  - 本次调度任务唯一 ID
  - 后续算法服务回调时必须原样带回

- `model_type`
  - 字符串
  - 当前调度模型标识
  - 例如：
    - `gpt2`
    - `tinyllama`
    - `llama-3.2-3b`

- `state_vector`
  - `list[float]`
  - 当前实现为 **26 维状态向量**
  - 算法服务只需要按既定模型输入处理即可
  - 上面示例里已按 26 个浮点数完整展开

### 云端后端当前行为

- 请求超时设置：`2.0` 秒
- 只要求算法服务**快速返回 2xx**
- 云端后端不会在这个同步响应里读取最终策略结果
- 真正的策略结果必须通过回调接口提交

### 建议同步响应

推荐算法服务收到请求后立即返回：

```json
{
  "status": "accepted"
}
```

只要 HTTP 状态码是 `2xx`，当前云端后端就会视为“算法服务已受理”。

---

## 2. 算法服务 -> 云端后端回调

### 回调地址

```http
POST /api/v1/schedule/strategy_callback
```

示例：

```text
http://10.144.144.2:8010/api/v1/schedule/strategy_callback
```

### 请求头

```http
Content-Type: application/json
```

### 回调请求体格式

```json
{
  "task_id": "87534955-99cb-4eed-8232-3dbfcd83010a",
  "model_type": "llama-3.2-3b",
  "layer_partitions": [
    {
      "layer_id": 0,
      "head_assignments": [0, 1, 0, 1],
      "ffn_assignment": 0
    },
    {
      "layer_id": 1,
      "head_assignments": [1, 0, 1, 0],
      "ffn_assignment": 1
    }
  ]
}
```

### 字段说明

- `task_id`
  - 必填
  - 必须与云端后端最初请求中的 `task_id` 完全一致

- `model_type`
  - 必填
  - 建议与请求中的 `model_type` 保持一致

- `layer_partitions`
  - 必填
  - 每一层的切分决策列表

#### `layer_partitions[*]` 格式

```json
{
  "layer_id": 0,
  "head_assignments": [0, 1, 0, 1],
  "ffn_assignment": 0
}
```

字段含义：

- `layer_id`
  - 当前层编号

- `head_assignments`
  - 当前层 attention heads 的分配结果
  - `0` 表示分配到边端
  - `1` 表示分配到云端

- `ffn_assignment`
  - 当前层 FFN 的分配结果
  - `0` 表示边端
  - `1` 表示云端
  - `2` 表示拆分

---

## 3. 云端后端收到回调后的行为

当回调成功后，云端后端会：

1. 用 `task_id` 匹配挂起中的调度任务
2. 保存策略结果
3. 查找边端 runtime 和云端 runtime
4. 向两边下发 `/load_strategy`
5. 等待两边 runtime 回调加载进度

因此，对算法服务来说，最关键的是：

- `task_id` 必须准确
- `layer_partitions` 结构必须符合格式

---

## 4. 成功响应

云端后端回调接口成功时返回：

```json
{
  "status": "success",
  "message": "任务 87534955-99cb-4eed-8232-3dbfcd83010a 切分策略已成功接收并交付"
}
```

---

## 5. 失败情况

### 任务不存在或超时

如果回调时 `task_id` 已失效，云端后端会返回：

```json
{
  "detail": "未找到对应的任务ID，或任务已超时废弃"
}
```

对应 HTTP 状态码：

```text
404
```

当前云端后端在发出算法请求后，会等待回调结果：

- 最长等待时间：`30` 秒

所以建议算法服务在 30 秒内完成回调。

---

## 6. 对接建议

算法同学侧最少只需要保证两件事：

1. 能接收以下格式的计算请求

```json
{
  "task_id": "string",
  "model_type": "string",
  "state_vector": [
    0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10,
    0.11, 0.12, 0.13, 0.14, 0.15, 0.16, 0.17, 0.18, 0.19, 0.20,
    0.21, 0.22, 0.23, 0.24, 0.25, 0.26
  ]
}
```

2. 能按以下格式回调策略结果

```json
{
  "task_id": "string",
  "model_type": "string",
  "layer_partitions": [
    {
      "layer_id": 0,
      "head_assignments": [0, 1, 0, 1],
      "ffn_assignment": 0
    }
  ]
}
```

一句话总结：

**云端后端发 `task_id + model_type + 26维 state_vector`，算法服务回 `task_id + model_type + layer_partitions`。**
