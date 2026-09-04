FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e .
ENV TRADING_MODE=paper
EXPOSE 8765
CMD ["python", "-m", "pa.main"]
