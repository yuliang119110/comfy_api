# Workflow Templates

Place ComfyUI workflow JSON files here.

## Naming Convention

  <task_name>.json                   -> default template for a task
  <task_name>_<model_name>.json      -> model-specific override

## Supported task_name values

| task_name         | Description                        |
|-------------------|------------------------------------|
| txt2img           | Text to Image                      |
| img2img           | Image to Image                     |
| txt_img2img       | Text + Image to Image              |
| txt2vid           | Text to Video                      |
| img2vid           | Image to Video                     |
| vid2vid           | Video to Video                     |
| txt_img2vid       | Text + Image to Video              |
| txt_img_vid2vid   | Text + Image + Video to Video      |

If no template is found, built-in fallback node graphs are used automatically.
