"""Local Flask prototype. Every request carries its own Mission and protocol."""
import base64
import csv
import hashlib
import io
import json
import os
import secrets
from pathlib import Path

from flask import Flask, Response, render_template, request, send_file
from itsdangerous import BadData, URLSafeTimedSerializer
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from datasets import BASE, CATEGORIES, MISSIONS, DataError, load_config, load_dataset, read_csv, timestamp
from evaluator import evaluate
from validation import parse_csv, validate_records


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("ESA_SECRET_KEY") or secrets.token_hex(32),
        SETTINGS_FILE=BASE / "configs" / "settings.yaml", CONFIG_DIR=BASE / "configs",
        MAX_CSV_BYTES=int(os.environ.get("ESA_MAX_CSV_BYTES", 5 * 1024 * 1024)),
        MAX_RECORDS=int(os.environ.get("ESA_MAX_RECORDS", 100000)),
        TOKEN_MAX_AGE=3600,
    )
    if test_config:
        app.config.update(test_config)
    # Signed preview includes the complete upload, not just the first 100 rows.
    app.config.setdefault("MAX_CONTENT_LENGTH", app.config["MAX_CSV_BYTES"] * 4 + 1024 * 1024)
    if app.config["MAX_CONTENT_LENGTH"] is None:
        app.config["MAX_CONTENT_LENGTH"] = app.config["MAX_CSV_BYTES"] * 4 + 1024 * 1024
    # Flask otherwise limits an individual non-file form field to 500 kB,
    # which would reject a valid large signed CSV preview on the second POST.
    app.config["MAX_FORM_MEMORY_SIZE"] = app.config["MAX_CONTENT_LENGTH"]
    signer = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="esa-evaluation-v1")

    def config_for(mission, source, version=None):
        return load_config(mission, source, version, app.config["CONFIG_DIR"], app.config["SETTINGS_FILE"])

    def seal(kind, payload):
        return signer.dumps(dict(kind=kind, payload=payload))

    def unseal(token, kind):
        try:
            value = signer.loads(token, max_age=app.config["TOKEN_MAX_AGE"])
            if value["kind"] != kind:
                raise DataError("下载或预览数据类型不匹配")
            return value["payload"]
        except (BadData, KeyError) as exc:
            raise DataError("预览或下载凭据已失效，请重新上传并评测（有效期 1 小时）") from exc

    def fingerprint(dataset, category):
        stamps = {k: (Path(v).stat().st_mtime_ns, Path(v).stat().st_size)
                  for k, v in dataset["config"]["paths"].items()}
        return hashlib.sha256(json.dumps([dataset["config"], category, stamps], sort_keys=True).encode()).hexdigest()

    @app.template_filter("score")
    def score_filter(value):
        return "不适用" if value is None else f"{value:.6f}"

    @app.route("/", methods=["GET", "POST"])
    def index():
        values = request.form if request.method == "POST" else request.args
        mission, source = values.get("mission", "Mission1"), values.get("source", "real")
        category, mode = values.get("category", "both"), values.get("mode", "manual")
        view = dict(missions=MISSIONS, mission=mission, source=source, category=category, mode=mode,
                    cfg=None, dataset=None, error=None, report=None, result=None, result_token=None,
                    upload_token="", errors_token=None, rows=[], timeline=[],
                    range_start=values.get("range_start", ""), range_end=values.get("range_end", ""),
                    range_confirmed=values.get("confirm_range") == "yes",
                    max_bytes=app.config["MAX_CSV_BYTES"], max_records=app.config["MAX_RECORDS"])
        if request.method == "POST":
            channels = request.form.getlist("channel")
            starts, ends = request.form.getlist("starttime"), request.form.getlist("endtime")
            view["rows"] = [dict(channel=c, starttime=starts[i] if i < len(starts) else "",
                                 endtime=ends[i] if i < len(ends) else "") for i, c in enumerate(channels)]
        try:
            if category not in CATEGORIES or mode not in ("manual", "csv"):
                raise DataError("事件类别或输入模式无效")
            if request.method == "POST" and not values.get("version"):
                raise DataError("缺少数据版本，请重新加载页面")
            cfg = config_for(mission, source, values.get("version") if request.method == "POST" else None)
            view["cfg"] = cfg
            if request.method == "GET":
                view["range_start"] = values.get("range_start", cfg.get("start") or "")
                view["range_end"] = values.get("range_end", cfg.get("end") or "")
            kwargs = {}
            if view["range_start"] or view["range_end"]:
                try:
                    uses_configured_range = bool(cfg.get("start") and cfg.get("end")) and (
                        timestamp(view["range_start"]) == timestamp(cfg["start"]) and
                        timestamp(view["range_end"]) == timestamp(cfg["end"]))
                except (ValueError, OverflowError):
                    uses_configured_range = False
                if not uses_configured_range:
                    kwargs = dict(start=view["range_start"], end=view["range_end"], confirmed=view["range_confirmed"])
            dataset = load_dataset(cfg, require_range=request.method == "POST", **kwargs)
            view.update(dataset=dataset, cfg=dataset["config"])
            if request.method == "GET":
                view["rows"] = [dict(channel=dataset["channels"][0], starttime="", endtime="")]
            else:
                context = fingerprint(dataset, category)
                if mode == "csv":
                    upload = request.files.get("prediction_csv")
                    if upload and upload.filename:
                        content = upload.read(app.config["MAX_CSV_BYTES"] + 1)
                        filename = secure_filename(upload.filename) or "predictions.csv"
                    elif values.get("upload_token"):
                        saved = unseal(values["upload_token"], "upload")
                        if saved["context"] != context:
                            raise DataError("Mission、版本、类别、窗口或标签已变化；旧预览失效，请重新上传校验")
                        filename = saved["filename"]
                        content = base64.b64decode(saved["content"])
                    else:
                        raise DataError("请选择预测 CSV 文件（空预测可上传只有合法表头的文件）")
                    report = parse_csv(content, filename, dataset, app.config["MAX_CSV_BYTES"], app.config["MAX_RECORDS"])
                    if len(content) <= app.config["MAX_CSV_BYTES"]:
                        view["upload_token"] = seal("upload", dict(context=context, filename=filename,
                                                                 content=base64.b64encode(content).decode("ascii")))
                else:
                    report = validate_records(view["rows"], dataset, app.config["MAX_RECORDS"])
                view["report"] = report
                if report["errors"]:
                    view["errors_token"] = seal("errors", report["errors"])
                elif values.get("action", "evaluate") == "evaluate":
                    result = evaluate(dataset, report["records"], category)
                    result["input_summary"] = {k: v for k, v in report.items() if k not in ("records", "preview")}
                    result["input_mode"] = mode
                    view.update(result=result, result_token=seal("result", result), timeline=make_timeline(result))
        except (DataError, OSError) as exc:
            prefix = "真实数据未配置或配置不完整：" if source == "real" else "配置或输入错误："
            view["error"] = prefix + str(exc)
        return render_template("index.html", **view)

    @app.get("/example")
    def example():
        try:
            mission, source = request.args.get("mission"), request.args.get("source")
            cfg = config_for(mission, source, request.args.get("version"))
            if source != "demo":
                raise DataError("示例仅适用于对应的演示配置；真实数据请提交算法预测")
            _, rows = read_csv(BASE / "demo_data" / mission / "predictions.csv")
            return dict(rows=rows, version=cfg["version"], mission=mission)
        except DataError as exc:
            return dict(error=str(exc)), 400

    @app.get("/demo/<mission>/<kind>.csv")
    def demo_csv(mission, kind):
        if mission not in MISSIONS or kind not in ("template", "predictions"):
            return "示例文件不存在", 404
        return send_file(BASE / "demo_data" / mission / f"{kind}.csv", as_attachment=True,
                         download_name=f"DEMO-{mission}-{kind}.csv", mimetype="text/csv")

    @app.post("/download/<kind>")
    def download(kind):
        try:
            if kind == "errors":
                errors = unseal(request.form.get("token", ""), "errors")
                return csv_response("validation-errors.csv", ["line", "message"],
                                    [[e["line"], e["message"]] for e in errors])
            result = unseal(request.form.get("token", ""), "result")
            cfg = result["config"]
            filename = f"{cfg['mission_id']}-{cfg['version']}-{cfg['source']}"
            if kind == "json":
                return Response(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False),
                                mimetype="application/json", headers={"Content-Disposition": f'attachment; filename="{secure_filename(filename)}.json"'})
            if kind == "events":
                return csv_response(filename + "-events.csv", ["mission", "version", "source", "ID", "Category", "channels", "detected", "intervals"],
                                    [[cfg["mission_id"], cfg["version"], cfg["source"], e["id"], e["category"],
                                      ";".join(e["channels"]), e["detected"], json.dumps(e["intervals"])] for e in result["events"]])
            raise DataError("下载格式无效")
        except DataError as exc:
            return render_template("error.html", message=str(exc)), 400

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_):
        return render_template("error.html", message=f"请求体超过上限。CSV 文件上限 {app.config['MAX_CSV_BYTES']} 字节，记录上限 {app.config['MAX_RECORDS']}；请缩小文件后重试。"), 413

    @app.after_request
    def no_cache(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    return app


def csv_response(filename, header, rows):
    text = io.StringIO(newline="")
    writer = csv.writer(text)
    writer.writerow(header)
    for row in rows:
        # Avoid spreadsheet formula interpretation in downloadable user-derived text.
        writer.writerow(["'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v for v in row])
    return Response("\ufeff" + text.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{secure_filename(filename)}"'})


def make_timeline(result):
    cfg = result["config"]
    a, b = timestamp(cfg["start"]).value, timestamp(cfg["end"]).value
    bars = []
    for lane, rows in (("truth", result["label_fragments"]), ("prediction", result["input_predictions"])):
        for row in rows:
            start, end = timestamp(row["start"]).value, timestamp(row["end"]).value
            bars.append(dict(x=60 + 900 * (start-a)/(b-a), width=900 * (end-start)/(b-a),
                             point=start == end, y=35 if lane == "truth" else 85,
                             channel=row["channel"], kind=lane if row.get("selected", True) else "excluded",
                             title=f"{row.get('id', '预测')} | {row['channel']} | {row.get('category', '')} | {row['start']} → {row['end']}"))
    return bars


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False)
