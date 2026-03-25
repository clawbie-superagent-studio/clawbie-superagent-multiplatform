"""图片处理工具集 - 供 Clawbie LocalTool 调用的原子函数"""

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path


# ── 即梦 API 签名 ────────────────────────────────────────────

def _hmac_sha256(key, msg):
    if isinstance(key, str):
        key = key.encode("utf-8")
    if isinstance(msg, str):
        msg = msg.encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).digest()


def _call_volc(ak, sk, action, body_dict):
    import urllib.request

    now = datetime.now(timezone.utc)
    date = now.strftime("%Y%m%dT%H%M%SZ")
    short_date = now.strftime("%Y%m%d")
    host = "visual.volcengineapi.com"
    query = f"Action={action}&Version=2022-08-31"
    body = json.dumps(body_dict).encode()
    payload_hash = hashlib.sha256(body).hexdigest()

    canonical_headers = (
        f"content-type:application/json\n"
        f"host:{host}\n"
        f"x-content-sha256:{payload_hash}\n"
        f"x-date:{date}\n"
    )
    signed_headers = "content-type;host;x-content-sha256;x-date"
    canonical_request = f"POST\n/\n{query}\n{canonical_headers}\n{signed_headers}\n{payload_hash}"

    credential_scope = f"{short_date}/cn-north-1/cv/request"
    string_to_sign = (
        f"HMAC-SHA256\n{date}\n{credential_scope}\n"
        f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    )

    k = _hmac_sha256(sk, short_date)
    k = _hmac_sha256(k, "cn-north-1")
    k = _hmac_sha256(k, "cv")
    k = _hmac_sha256(k, "request")
    sig = hmac.new(k, string_to_sign.encode(), hashlib.sha256).hexdigest()

    auth = f"HMAC-SHA256 Credential={ak}/{credential_scope}, SignedHeaders={signed_headers}, Signature={sig}"
    headers = {
        "Content-Type": "application/json",
        "Host": host,
        "X-Date": date,
        "X-Content-Sha256": payload_hash,
        "Authorization": auth,
    }

    url = f"https://{host}/?{query}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def _get_jimeng_keys():
    ak = os.environ.get("JIMENG_AK", "")
    sk = os.environ.get("JIMENG_SK", "")
    if not ak or not sk:
        return None, None, (
            "未配置即梦 API Key。请引导用户操作：\n"
            "1. 打开 My Claw → 设置（齿轮图标）\n"
            "2. 切换到「自带 API Key」模式\n"
            "3. 滚动到底部「图片模型」区域\n"
            "4. 填写即梦 Access Key 和 Secret Key\n"
            "5. AK/SK 从火山引擎控制台获取：console.volcengine.com → 即梦 AI\n"
            "6. 点击 Save 保存后重试"
        )
    return ak, sk, None


# ── 工具 0: 即梦文生图 ─────────────────────────────────────

def text_to_image(prompt, output_path, width=512, height=512):
    """使用即梦 AI 根据文字描述生成图片"""
    ak, sk, err = _get_jimeng_keys()
    if err:
        return {"ok": False, "error": err}

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # 提交任务
    result = _call_volc(ak, sk, "CVSync2AsyncSubmitTask", {
        "req_key": "jimeng_t2i_v40",
        "prompt": prompt,
        "width": width,
        "height": height,
    })

    code = result.get("code")
    task_id = result.get("data", {}).get("task_id", "") if result.get("data") else ""
    if code != 10000 or not task_id:
        err_msg = json.dumps(result, ensure_ascii=False)[:300]
        if "SignatureDoesNotMatch" in err_msg:
            return {"ok": False, "error": "即梦 API 签名不匹配，请检查设置中的 AK/SK 是否正确。"}
        if "Access Denied" in err_msg:
            return {"ok": False, "error": "即梦 API 访问被拒绝，请确认火山引擎账号已开通即梦 AI 服务。"}
        return {"ok": False, "error": f"即梦提交失败: {err_msg}"}

    # 轮询结果
    import time
    for _ in range(20):
        time.sleep(5)
        r2 = _call_volc(ak, sk, "CVSync2AsyncGetResult", {
            "req_key": "jimeng_t2i_v40",
            "task_id": task_id,
        })
        status = r2.get("data", {}).get("status", "") if r2.get("data") else ""
        if status == "done":
            b64_list = r2["data"].get("binary_data_base64") or []
            if b64_list:
                img_data = base64.b64decode(b64_list[0])
                with open(output_path, "wb") as f:
                    f.write(img_data)
                return {"ok": True, "output": output_path, "size_kb": len(img_data) // 1024}
            return {"ok": False, "error": "即梦返回成功但无图片数据"}
        if "error" in str(status).lower() or "fail" in str(status).lower():
            return {"ok": False, "error": f"即梦生成失败: {status}"}

    return {"ok": False, "error": "即梦生成超时（等待超过 100 秒）"}


# ── 工具 1: 即梦图生图风格转换 ───────────────────────────────

def style_transfer(input_path, output_path, prompt, scale=0.5):
    """使用即梦 AI 将图片转换为指定风格"""
    ak, sk, err = _get_jimeng_keys()
    if err:
        return {"ok": False, "error": err}

    src = Path(input_path)
    if not src.exists():
        return {"ok": False, "error": f"输入文件不存在: {input_path}"}

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    with open(src, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    # 提交任务
    result = _call_volc(ak, sk, "CVSync2AsyncSubmitTask", {
        "req_key": "jimeng_t2i_v40",
        "prompt": prompt,
        "binary_data_base64": [img_b64],
        "scale": scale,
    })

    code = result.get("code")
    task_id = result.get("data", {}).get("task_id", "") if result.get("data") else ""
    if code != 10000 or not task_id:
        err_msg = json.dumps(result, ensure_ascii=False)[:300]
        if "SignatureDoesNotMatch" in err_msg:
            return {"ok": False, "error": "即梦 API 签名不匹配，请检查设置中的 AK/SK 是否正确。"}
        if "Access Denied" in err_msg:
            return {"ok": False, "error": "即梦 API 访问被拒绝，请确认火山引擎账号已开通即梦 AI 服务。"}
        return {"ok": False, "error": f"即梦提交失败: {err_msg}"}

    # 轮询结果
    import time
    for _ in range(20):
        time.sleep(5)
        r2 = _call_volc(ak, sk, "CVSync2AsyncGetResult", {
            "req_key": "jimeng_t2i_v40",
            "task_id": task_id,
        })
        status = r2.get("data", {}).get("status", "") if r2.get("data") else ""
        if status == "done":
            b64_list = r2["data"].get("binary_data_base64") or []
            if b64_list:
                img_data = base64.b64decode(b64_list[0])
                with open(output_path, "wb") as f:
                    f.write(img_data)
                return {"ok": True, "output": output_path, "size_kb": len(img_data) // 1024}
            return {"ok": False, "error": "即梦返回成功但无图片数据"}
        if "error" in str(status).lower() or "fail" in str(status).lower():
            return {"ok": False, "error": f"即梦生成失败: {status}"}

    return {"ok": False, "error": "即梦生成超时（等待超过 100 秒）"}


# ── 工具 2: Pillow 像素级调色 ────────────────────────────────

def recolor(input_path, output_path, target_r, target_g, target_b,
            match_r_min=0, match_r_max=255, match_g_min=0, match_g_max=255,
            match_b_min=0, match_b_max=255):
    """对图片进行像素级颜色替换，保留明暗层次和透明通道"""
    try:
        from PIL import Image
        import numpy as np
    except ImportError as e:
        return {"ok": False, "error": (
            f"缺少 Python 依赖: {e.name}。请引导用户在终端执行：\n"
            "pip3 install Pillow numpy\n"
            "安装完成后重试即可。"
        )}

    src = Path(input_path)
    if not src.exists():
        return {"ok": False, "error": f"输入文件不存在: {input_path}"}

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    img = Image.open(str(src)).convert("RGBA")
    arr = np.array(img, dtype=np.float64)

    for y in range(arr.shape[0]):
        for x in range(arr.shape[1]):
            r, g, b, a = arr[y, x]
            if a <= 10:
                continue
            if (match_r_min <= r <= match_r_max
                    and match_g_min <= g <= match_g_max
                    and match_b_min <= b <= match_b_max):
                lum = (r * 0.299 + g * 0.587 + b * 0.114) / 255.0
                arr[y, x] = [
                    min(255, target_r * lum),
                    min(255, target_g * lum),
                    min(255, target_b * lum),
                    a,
                ]

    w, h = img.size
    Image.fromarray(arr.astype(np.uint8)).save(output_path)
    return {"ok": True, "output": output_path, "size": f"{w}x{h}"}


# ── 工具 3: 图片尺寸匹配/缩放 ───────────────────────────────

def resize(input_path, output_path, width=0, height=0, reference_path=""):
    """将图片缩放到指定尺寸或匹配参考图尺寸"""
    try:
        from PIL import Image
    except ImportError:
        return {"ok": False, "error": (
            "缺少 Python 依赖: Pillow。请引导用户在终端执行：\n"
            "pip3 install Pillow\n"
            "安装完成后重试即可。"
        )}

    src = Path(input_path)
    if not src.exists():
        return {"ok": False, "error": f"输入文件不存在: {input_path}"}

    if reference_path:
        ref = Path(reference_path)
        if not ref.exists():
            return {"ok": False, "error": f"参考文件不存在: {reference_path}"}
        ref_img = Image.open(str(ref))
        width, height = ref_img.size
        ref_img.close()
    elif not width or not height:
        return {"ok": False, "error": "请指定 width/height 或 reference_path"}

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    img = Image.open(str(src))
    orig_w, orig_h = img.size

    if orig_w == width and orig_h == height:
        if str(src) != output_path:
            img.save(output_path)
        img.close()
        return {"ok": True, "output": output_path, "note": f"已经是 {width}x{height}，无需缩放"}

    result = img.resize((width, height), Image.LANCZOS)
    result.save(output_path)
    img.close()
    result.close()
    return {"ok": True, "output": output_path, "from": f"{orig_w}x{orig_h}", "to": f"{width}x{height}"}
