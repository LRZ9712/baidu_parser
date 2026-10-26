FROM python:3.11-slim

WORKDIR /app

# 设置时区为上海
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 拷贝项目代码
COPY . .

# 暴露端口
EXPOSE 8002

# 启动服务
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]