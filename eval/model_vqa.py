import argparse
import torch
import os
import json
from tqdm import tqdm
import shortuuid

from llava.constants import IMAGE_TOKEN_INDEX, DEFAULT_IMAGE_TOKEN, DEFAULT_IM_START_TOKEN, DEFAULT_IM_END_TOKEN
from llava.conversation import conv_templates, SeparatorStyle
from llava.model.builder import load_pretrained_model
from llava.utils import disable_torch_init
from llava.mm_utils import tokenizer_image_token, get_model_name_from_path, KeywordsStoppingCriteria

from PIL import Image
import math


def split_list(lst, n):
    """Split a list into n (roughly) equal-sized chunks"""
    chunk_size = math.ceil(len(lst) / n)  # integer division
    return [lst[i:i+chunk_size] for i in range(0, len(lst), chunk_size)]


def get_chunk(lst, n, k):
    chunks = split_list(lst, n)
    return chunks[k]


def eval_model(args):
    # Model
    disable_torch_init()
    model_path = os.path.expanduser(args.model_path)
    model_name = get_model_name_from_path(model_path)
    #model_name = 'llava-v1.5-13b-task-lora_v1_add'
    # model_name = 'llava-v1.5-7b-task-lora'
    tokenizer, model, image_processor, context_len = load_pretrained_model(model_path, args.model_base, model_name)

    # print("image_processor:", image_processor)

    questions = [json.loads(q) for q in open(os.path.expanduser(args.question_file), "r")]
    questions = get_chunk(questions, args.num_chunks, args.chunk_idx)
    answers_file = os.path.expanduser(args.answers_file)
    os.makedirs(os.path.dirname(answers_file), exist_ok=True)
    ans_file = open(answers_file, "w")
    for line in tqdm(questions):
        idx = line["question_id"]
        image_file = line["image"]
        qs = line["text"]
        label = line["category"]
        cur_prompt = qs

        # print("image_file：", image_file)  # 测试
        # print("ordinal_qs:", qs)  # 测试

        # begin：添加的内容：判断 image_file 是单个字符串还是列表，并计算图像数量
        if isinstance(image_file, list):
            image_count = len(image_file)
        else:
            image_count = 1
        # 根据图像数量生成相应数量的 DEFAULT_IMAGE_TOKEN + '\n'
        image_tokens = (DEFAULT_IMAGE_TOKEN + '\n') * image_count
        # end

        # 根据模型的配置来更新 qs
        if model.config.mm_use_im_start_end:
            qs = DEFAULT_IM_START_TOKEN + DEFAULT_IMAGE_TOKEN + DEFAULT_IM_END_TOKEN + '\n' + qs
            # qs = DEFAULT_IM_START_TOKEN + DEFAULT_IMAGE_TOKEN * image_count + DEFAULT_IM_END_TOKEN + '\n' + qs
        else:
            # qs = image_tokens + qs + '\n' + 'Answer the question using a single word or phrase.'
            qs = image_tokens + qs
            # print("new_qs:", qs)
            # qs = DEFAULT_IMAGE_TOKEN + '\n' + qs + '\n' + 'Answer the question using a single word or phrase.'  # 原始的code

        conv = conv_templates[args.conv_mode].copy()
        conv.append_message(conv.roles[0], qs)
        conv.append_message(conv.roles[1], None)
        prompt = conv.get_prompt()
        # print("prompt:", prompt)

        input_ids = tokenizer_image_token(prompt, tokenizer, IMAGE_TOKEN_INDEX, return_tensors='pt').unsqueeze(0).cuda()
        # print("input_ids:", input_ids)

        ### 此处对其进行了修改，将其改成可以读取多张图像文件，原来的在else中
        if isinstance(image_file, list):
            image = []
            for img_file in image_file:
                image.append(Image.open(os.path.join(args.image_folder, img_file)).convert('RGB'))  # [2, 3, 336,336]
        else:
            image = Image.open(os.path.join(args.image_folder, image_file)).convert('RGB')
            # image = Image.open(os.path.join(args.image_folder, image_file))

        ### 此处对其进行了修改，将其改成可以读取多张图像文件,原code在else中
        if isinstance(image, list):
            image_tensor = [image_processor.preprocess(img, return_tensors='pt')['pixel_values'][0] for img in image]
        else:
            image_tensor = image_processor.preprocess(image, return_tensors='pt')['pixel_values'][0]


        stop_str = conv.sep if conv.sep_style != SeparatorStyle.TWO else conv.sep2
        # keywords = [stop_str]
        # stopping_criteria = KeywordsStoppingCriteria(keywords, tokenizer, input_ids)
        # 此处改成可以接收多张图像的列表
        if not isinstance(image_tensor, list):
            image_tensor = image_tensor.unsqueeze(0).half().cuda()
            # print("image_tensor.shape:", image_tensor.shape)
        else:
            # 处理图像列表
            processed_images = [img.unsqueeze(0).half().cuda() for img in image_tensor]
            # 将处理后的图像张量连接在一起，形成一个批次
            image_tensor = torch.cat(processed_images, dim=0)
            # 确保最终的 image_tensor 在 cuda 设备上
            image_tensor = image_tensor.cuda()
            # 在维度0位置添加一个新的维度
            image_tensor = image_tensor.unsqueeze(0)

            # print("image_tensor.shape:", image_tensor.shape)

            """
            # 处理图像列表
            processed_images = [img.unsqueeze(0).half().cuda() for img in image_tensor]
            # 将处理后的图像张量连接在一起，形成一个批次
            image_tensor = torch.cat(processed_images, dim=0)
            # 确保最终的 image_tensor 在 cuda 设备上
            image_tensor = image_tensor.cuda()
            print("image_tensor.shape:", image_tensor.shape)
"""

        with torch.inference_mode():
            output_ids = model.generate(
                input_ids,
                images=image_tensor,
                do_sample=True if args.temperature > 0 else False,
                temperature=args.temperature,
                top_p=args.top_p,
                num_beams=args.num_beams,
                # no_repeat_ngram_size=3,
                max_new_tokens=2048,
                # max_new_tokens=1024, 最新的代码
                use_cache=True)

        input_token_len = input_ids.shape[1]
        n_diff_input_output = (input_ids != output_ids[:, :input_token_len]).sum().item()
        if n_diff_input_output > 0:
            print(f'[Warning] {n_diff_input_output} output_ids are not the same as the input_ids')
        outputs = tokenizer.batch_decode(output_ids[:, input_token_len:], skip_special_tokens=True)[0]
        # print("好奇这里的outputs：", outputs)
        outputs = outputs.strip()
        if outputs.endswith(stop_str):
            outputs = outputs[:-len(stop_str)]
        outputs = outputs.strip()
        # print("最终的outputs:", outputs)

        ans_id = shortuuid.uuid()
        ans_file.write(json.dumps({"question_id": idx,
                                   "prompt": cur_prompt,
                                   "text": outputs,
                                   "label": label,
                                   "answer_id": ans_id,
                                   "model_id": model_name,
                                   "metadata": {}}) + "\n")
        ans_file.flush()
    ans_file.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", type=str, default="facebook/opt-350m")
    parser.add_argument("--model-base", type=str, default=None)
    parser.add_argument("--image-folder", type=str, default="")
    parser.add_argument("--question-file", type=str, default="tables/sleep_question.jsonl")
    parser.add_argument("--answers-file", type=str, default="answer.jsonl")
    parser.add_argument("--conv-mode", type=str, default="llava_v1")
    parser.add_argument("--num-chunks", type=int, default=1)
    parser.add_argument("--chunk-idx", type=int, default=0)
    parser.add_argument("--temperature", type=float, default=0.1)#原来是0.2
    parser.add_argument("--top_p", type=float, default=None)
    parser.add_argument("--num_beams", type=int, default=1)
    #parser.add_argument("--ckp", type=str, default=)
    args = parser.parse_args()

    eval_model(args)
