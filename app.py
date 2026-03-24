import pandas as pd
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
import traceback

app = Flask(__name__, static_folder='.')
CORS(app)

# ========== 请根据实际情况修改 Excel 文件路径 ==========
EXCEL_PATH = "欧洲电价综合.xlsx"   # 如果文件在桌面，直接写文件名
# 如果文件在其他位置，使用绝对路径：
# EXCEL_PATH = r"C:\Users\Hanyu\Desktop\数据更新\综合电价 每周更新\欧洲电价综合.xlsx"
# ====================================================

def load_excel_data():
    """读取 Excel 并转换为前端可用的 JSON 格式"""
    df = pd.read_excel(EXCEL_PATH, sheet_name=0)

    # 找到日期列
    date_col = None
    for col in df.columns:
        if col.lower() == 'date':
            date_col = col
            break
    if date_col is None:
        raise ValueError("Excel 中未找到 'Date' 列")

    # 解析日期（自动识别 dd/mm/yy HH:MM 格式）
    df['datetime'] = pd.to_datetime(df[date_col], dayfirst=True, errors='coerce')
    df = df.dropna(subset=['datetime'])
    if df.empty:
        raise ValueError("没有有效的日期数据，请检查日期格式")

    # 提取年、月、小时（确保整数）
    df['year'] = df['datetime'].dt.year.astype(int)
    df['month'] = df['datetime'].dt.month.astype(int)
    df['hour'] = df['datetime'].dt.hour.astype(int)

    # 识别国家列（除日期列和辅助列外，尝试转换为数值）
    exclude = [date_col, 'datetime', 'year', 'month', 'hour']
    country_cols = []
    for col in df.columns:
        if col in exclude:
            continue
        # 尝试转为数值，如果失败则跳过该列
        try:
            df[col] = pd.to_numeric(df[col], errors='coerce')
            # 至少有一个有效数值才保留
            if df[col].notna().any():
                country_cols.append(col)
        except:
            continue

    if not country_cols:
        raise ValueError("未找到任何有效价格列")

    # 转换为记录列表，确保 JSON 可序列化
    records = []
    for _, row in df.iterrows():
        values = {}
        for c in country_cols:
            v = row[c]
            if pd.isna(v):
                values[c] = None
            else:
                # 转换为 Python 原生 float
                values[c] = float(v)
        rec = {
            'datetime': row['datetime'].isoformat(),
            'year': int(row['year']),
            'month': int(row['month']),
            'hour': int(row['hour']),
            'values': values
        }
        records.append(rec)

    return {
        'data': records,
        'years': sorted(df['year'].unique().tolist()),
        'countries': sorted(country_cols)
    }

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/api/data')
def get_data():
    try:
        data = load_excel_data()
        return jsonify(data)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)