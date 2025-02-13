import pandas as pd
import json

# 读取CSV文件
csv_file = 'labels.csv'  # 修改为你的CSV文件路径
df = pd.read_csv(csv_file)

# 打乱数据

df = df.sample(frac=1).reset_index(drop=True)

# 定义问题模板
def create_question(label):
    if label == 0:
        return "Wake"
    elif label == 1:
        return "N1"
    elif label == 2:
        return "N2"
    elif label == 3:
        return "N3"
    elif label == 4:
        return "Rem"
    else:
        return "default."

# 转换为jsonl格式并保存
with open('question.jsonl', 'w', encoding='utf-8') as jsonl_file:
    for index, row in df.iterrows():
        data = {
            "question_id": str(index),
            "image": row['Image'],
            "text": "Based on the spectrogram provided for 30 seconds of sleep audio,"
                    " which sleep stage does it most likely represent? "
                    "Please do not explain; "
                    "just choose the sleep stage you are most certain:"
                    " wake, N1, N2, N3, or REM. "
                    "Answer the question using a single word or phrase.",
            "category": f"{create_question(row['Label'])}"
        }
        jsonl_file.write(json.dumps(data) + '\n')

print("Conversion completed.")
