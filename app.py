def generate_with_retries(func, max_retries=3):
    for attempt in range(max_retries):
        try:
            return func()
        except ResourceExhaustedError:
            if attempt < max_retries - 1:
                continue    
    raise ResourceExhaustedError("Max retries exceeded")


def get_order_data(...):
    ...  # existing get_order_data logic
    try:
        ...  # code to get order data
    except ResourceExhaustedError:
        for image in images:
            save_image_to_pending(image)  # Function to save failing images
            
    return order_data


# Save images to pending directory function

def save_image_to_pending(image):
    path = f'pending/{image.filename}'
    with open(path, 'wb') as f:
        f.write(image.data)
