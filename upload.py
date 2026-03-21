from huggingface_hub import HfApi

api = HfApi()

print("Starting upload... this will take a while (4GB)")

api.upload_folder(
    folder_path=r"C:\Users\vinee\OneDrive\Desktop\project\fake_news_model",
    repo_id="vineet-jha24/fake-news-model",
    repo_type="model",
    token="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",
)

print("Upload complete!")