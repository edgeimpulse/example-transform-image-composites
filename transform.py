import os, sys, shutil
import requests
import argparse
import json
import time
import traceback
import random
from wand.image import Image
from wand.color import Color
import cv2
import numpy as np
from rembg import remove

if not os.getenv('EI_PROJECT_API_KEY'):
    print('Missing EI_PROJECT_API_KEY')
    sys.exit(1)

API_KEY = os.environ.get("EI_PROJECT_API_KEY")
INGESTION_HOST = os.environ.get("EI_INGESTION_HOST", "edgeimpulse.com")

# these are the three arguments that we get in
parser = argparse.ArgumentParser(description='Use OpenAI Dall-E to generate an image dataset for classification from your prompt')
parser.add_argument('--composite-dir', type=str, required=True, help="What folder are the source composite images found in? (there should be background and object folders)")
parser.add_argument('--remove-background', type=int, required=True, help="Do you have images of your objects which need the background removing?")
parser.add_argument('--raw-object-dir', type=str, required=False, help="What folder are the source composite images found in? (there should be background and object folders)")
parser.add_argument('--resize-raw-objects', type=int, required=False, help="This will match the size of the raw object images to the first background image in your background directory before extracting the objects (so that they are appropriately sized to be the same scale as the background images)")
parser.add_argument('--ignore-already-resized', type=int, required=False, help="If set to 1, will ignore already resized images in the object directory, otherwise they will be done again and overwritten", default=0)

parser.add_argument('--labels', type=str, required=True, help="Which objects to generate images for, as a comma-separated list. Set as 'all' to generate images for all objects")
parser.add_argument('--images', type=int, required=True, help="Number of images to generate")
parser.add_argument('--objects', type=int, required=True, help="Maximum number of objects to generate")
parser.add_argument('--allow-overlap', type=int, required=True, help="Whether objects are allowed to overlap")
parser.add_argument('--allow-rotate', type=int, required=True, help="Whether to apply random rotation to objects")
parser.add_argument('--apply-motion-blur', type=int, required=True, help="Whether to apply blur to objects to simulate motion")
parser.add_argument('--motion-blur-direction', type=int, required=False, help="What direction apply blur to objects to simulate motion (-1 for random)", default=-1)
parser.add_argument('--object-area', type=str, required=True, help="x1,y1,x2,y2 coordinates of the valid area to place objects in the composite image")

parser.add_argument('--apply-fisheye', type=int, required=True, help='Whether to apply fisheye lens effect to the final images')
parser.add_argument('--apply-fisheye-all-layers', type=int, required=False, help='Whether to apply fisheye lens effect to all layers or just to the objects')
parser.add_argument('--fisheye-strength', type=float, required=False, default=0.5, help='Whether to apply fisheye lens effect to the final images')
parser.add_argument('--crop-fisheye', type=int, required=False, help='Whether to apply fisheye lens effect to all layers or just to the objects')

parser.add_argument('--upload-category', type=str, required=False, help="Which category to upload data to in Edge Impulse", default='split')
parser.add_argument('--synthetic-data-job-id', type=int, required=False, help="If specified, sets the synthetic_data_job_id metadata key")
parser.add_argument('--skip-upload', type=bool, required=False, help="Skip uploading to EI", default=False)
parser.add_argument('--out-directory', type=str, required=False, help="Directory to save images to", default="output")
args, unknown = parser.parse_known_args()
if not os.path.exists(args.out_directory):
    os.makedirs(args.out_directory)
output_folder = args.out_directory


INGESTION_URL = "https://ingestion." + INGESTION_HOST
if (INGESTION_HOST.endswith('.test.edgeimpulse.com')):
    INGESTION_URL = "http://ingestion." + INGESTION_HOST
if (INGESTION_HOST == 'host.docker.internal'):
    INGESTION_URL = "http://" + INGESTION_HOST + ":4810"



if args.labels == 'all':
    labels = ['all']
else:
    labels = args.labels.split(',')

base_images_number = args.images
upload_category = args.upload_category
num_objects = args.objects
allow_overlap = args.allow_overlap
allow_rotate = args.allow_rotate
apply_motion_blur = args.apply_motion_blur
blur_direction = args.motion_blur_direction

bbox_json = {
    "version": 1,
    "type": "bounding-box-labels",
    "boundingBoxes": {
        
    }
}

# Check if the object area is in the correct format and parse as integers
if args.object_area == '-1':
    object_area = -1
else:
    object_area = args.object_area.split(',')
    if len(object_area) != 4:
        print('Invalid value for "--object-area", should be "x1,y1,x2,y2" (was: "' + args.object_area + '")')
        exit(1)
    try:
        object_area = [int(i) for i in object_area]
    except ValueError:
        print('Invalid value for "--object-area", should be "x1,y1,x2,y2" (was: "' + args.object_area + '")')
        exit(1)

bg_dir = os.path.join(args.composite_dir, 'background')
obj_dir = os.path.join(args.composite_dir, 'object')

# Check if the background and object directories exist
if not os.path.exists(bg_dir):
    print('Background directory not found:', bg_dir)
    #print directories under the composite directory
    print('Directories under the composite directory:', os.listdir(args.composite_dir))
    exit(1)
if not os.path.exists(obj_dir):
    print('Object directory not found:', obj_dir)
    exit(1)

def remove_background_and_crop(image_path, output_path):
    # Load the image
    image = cv2.imread(image_path)
    
    # Remove the background
    image_no_bg = remove(image)
    
    # Convert the image to a numpy array
    image_no_bg_np = np.array(image_no_bg)
    
    # Find the bounding box of the object
    gray = cv2.cvtColor(image_no_bg_np, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 1, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    x, y, w, h = cv2.boundingRect(contours[0])
    
    # Crop the image to the bounding box
    cropped_image = image_no_bg_np[y:y+h, x:x+w]
    
    # Save the result as a png with transparency
    cv2.imwrite(output_path, cropped_image)
    print(f'Cropped object with dimensions {w}x{h} to {output_path}')
# Function to apply fisheye effect
def apply_fisheye(image, strength=0.5, crop=True, crop_box=None):
    height, width = image.shape[:2]
    K = np.array([[width, 0, width / 2],
                  [0, height, height / 2],
                  [0, 0, 1]], dtype=np.float32)
    D = np.array([strength, strength, 0, 0], dtype=np.float32)
    map1, map2 = cv2.fisheye.initUndistortRectifyMap(K, D, np.eye(3), K, (width, height), cv2.CV_16SC2)
    fisheye_image = cv2.remap(image, map1, map2, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    
    if crop:
        if crop_box:
            x, y, w, h = crop_box
        else:
            # Find the bounding box of the non-black area
            gray = cv2.cvtColor(fisheye_image, cv2.COLOR_BGR2GRAY)
            _, thresh = cv2.threshold(gray, 1, 255, cv2.THRESH_BINARY)
            contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            x, y, w, h = cv2.boundingRect(contours[0])
            
        # Crop the image to the bounding box
        cropped_image = fisheye_image[y:y+h, x:x+w]
        
        # Scale the cropped image back to the original size
        scaled_image = cv2.resize(cropped_image, (width, height), interpolation=cv2.INTER_LINEAR)
        
        return scaled_image, (x, y, w, h)
    else:
        return fisheye_image, (0, 0, width, height)

# Function to adjust bounding boxes for fisheye effect
def adjust_bounding_boxes(objects, width, height, crop_box, strength=0.5):
    K = np.array([[width, 0, width / 2],
                  [0, height, height / 2],
                  [0, 0, 1]], dtype=np.float32)
    D = np.array([strength, strength, 0, 0], dtype=np.float32)
    new_objects = []
    crop_x, crop_y, crop_w, crop_h = crop_box
    scale_x = width / crop_w
    scale_y = height / crop_h
    
    for obj in objects:
        x, y, w, h = obj['x'], obj['y'], obj['width'], obj['height']
        points = np.array([[x, y], [x + w, y], [x, y + h], [x + w, y + h]], dtype=np.float32)
        points = np.expand_dims(points, axis=1)
        new_points = cv2.fisheye.undistortPoints(points, K, D, P=K)
        new_points = new_points.squeeze()
        if crop_box != (0, 0, width, height):
            new_points[:, 0] = (new_points[:, 0] - crop_x) * scale_x
            new_points[:, 1] = (new_points[:, 1] - crop_y) * scale_y
        new_x, new_y = np.min(new_points, axis=0)
        new_w, new_h = np.max(new_points, axis=0) - np.min(new_points, axis=0)
        new_objects.append({'label': obj['label'], 'x': int(new_x), 'y': int(new_y), 'width': int(new_w), 'height': int(new_h)})
    
    return new_objects

# Iterate through the background folder and load into a list of Image() objects if the files are images (png, bmp, jpg, jpeg)
bg_images = []
obj_images = []
for filename in os.listdir(bg_dir):
    if filename.endswith('.png') or filename.endswith('.bmp') or filename.endswith('.jpg') or filename.endswith('.jpeg'):
        bg_images.append(Image(filename=os.path.join(bg_dir, filename)))
        print('Loaded background image:', filename)

# Remove the background from the object images and save them in the object directory if the flag is set
if args.remove_background:
    if not args.raw_object_dir:
        print('Missing raw object directory')
        sys.exit(1)
    raw_obj_dir = args.raw_object_dir
    if not os.path.exists(raw_obj_dir):
        print('Raw object directory not found:', raw_obj_dir)
        sys.exit(1)
    for filename in os.listdir(raw_obj_dir):
        if filename.endswith('.png') or filename.endswith('.bmp') or filename.endswith('.jpg') or filename.endswith('.jpeg'):
            #change output filename to .png
            out_filename = filename.split('.')[0] + '.png'
            # Check if out_filename already exists in the object directory
            if os.path.exists(os.path.join(obj_dir, out_filename)) and args.ignore_already_resized:
                print('Object image already exists:', out_filename)
                continue

            if args.resize_raw_objects:
                # Resize the raw object image to the height of the first background image in the background directory maintaining the aspect ratio
                bg_width = bg_images[0].width
                bg_height = bg_images[0].height
                img = Image(filename=os.path.join(raw_obj_dir, filename))
                # Calculate the new width while maintaining the aspect ratio
                aspect_ratio = img.width / img.height
                new_width = int(bg_height * aspect_ratio)
                # Resize the image to match the bg_height while maintaining the aspect ratio
                img.resize(new_width, bg_height)
                img.save(filename=os.path.join(raw_obj_dir, filename))

                print(f'Reszied raw object image to {bg_width}x{bg_height}:', filename)
            remove_background_and_crop(os.path.join(raw_obj_dir, filename), os.path.join(obj_dir, out_filename))
            

# Iterate through the objects folder and load into a list of Image() objects if the files are images (png, bmp, jpg, jpeg)
for filename in os.listdir(obj_dir):
    if filename.endswith('.png') or filename.endswith('.bmp') or filename.endswith('.jpg') or filename.endswith('.jpeg'):
        # Check if the image filename (before the first underscore) is in the labels list, or if labels is set to 'all' (in which case we add all images)
        if labels == ['all'] or filename.split('_')[0] in labels:
            obj_images.append({"image": Image(filename=os.path.join(obj_dir, filename)), "label": filename.split('_')[0]})
            print('Loaded object image:', filename)






if (upload_category != 'split' and upload_category != 'training' and upload_category != 'testing'):
    print('Invalid value for "--upload-category", should be "split", "training" or "testing" (was: "' + upload_category + '")')
    exit(1)

output_folder = 'output/'
# Check if output directory exists and create it if it doesn't
if not os.path.exists(output_folder):
    os.makedirs(output_folder)
else:
    shutil.rmtree(output_folder)
    os.makedirs(output_folder)

epoch = int(time.time())

print('Number of images:', base_images_number)
print('Objects to be generated:', args.objects)
print('Allow overlap:', args.allow_overlap)
print('Object area:', object_area)
print('')

for i in range(base_images_number):
    objects = []
    background = bg_images[random.randrange(len(bg_images))].clone()
    # init the object layer with transparent background and same size as the background
    object_layer = Image(width=background.width, height=background.height, background=Color('transparent'))
    # Define the dimensions of the background image
    background_width = background.width
    background_height = background.height
    if object_area == -1:
        object_area = [0, 0, background_width, background_height]
    # Define the dimensions of the area where objects can be placed
    object_area_left = object_area[0]
    object_area_top = object_area[1]
    object_area_width = object_area[2] - object_area[0]
    object_area_height = object_area[3] - object_area[1]

    if apply_motion_blur:
        blur_amount = random.randrange(8)
        if args.motion_blur_direction == -1:
            blur_direction = random.choice([-90, 90])
        background.motion_blur(sigma=blur_amount, angle=blur_direction)

    # Create a new image for each object
    for i in range(random.randrange(num_objects)):
        # Load the object image
        object = obj_images[random.randrange(len(obj_images))]
        object_image = object['image'].clone()
        label = object['label']
        if allow_rotate:
            object_image.rotate(random.uniform(0, 360))

        object_width = object_image.width
        object_height = object_image.height

        object_image.resize(object_width, object_height)
        if apply_motion_blur:
            object_image.motion_blur(sigma=blur_amount, angle=blur_direction)

        # Place the object in a random position within the defined area

        # Ensure the object can fit within the defined area
        if object_area_width >= object_width and object_area_height >= object_height:
            x = random.randint(object_area_left, object_area_left + object_area_width - object_width)
            y = random.randint(object_area_top, object_area_top + object_area_height - object_height)
        else:
            # Handle the case where the object cannot fit within the defined area
            print("Error: Object cannot fit within the defined area.")
            continue

        # Check if the object overlaps with any previously placed objects
        overlap = False
        if not allow_overlap:
            if len(objects)>0:
                for j in range(len(objects)):
                    if (x < objects[j]['x'] + objects[j]['width'] and x + object_width > objects[j]['x'] and
                        y < objects[j]['y'] + objects[j]['height'] and y + object_height > objects[j]['y']):
                        overlap = True
                        break
        # If there is no overlap, place the object on the background image
        if not overlap:
            object_layer.composite(object_image, x, y)

            # Add the object's position and size to the list of placed objects
            objects.append({'label': label, 'x': x, 'y': y, 'width': object_width, 'height': object_height})

    print(f'Created image {i+1} of {base_images_number} with {len(objects)} objects', end='', flush=True)
    fullpath = os.path.join(args.out_directory,f'composite.{epoch}.{i}.png')

    if args.apply_fisheye:
        object_layer_np = np.array(object_layer)
        background_np = np.array(background)
        
        if args.apply_fisheye_all_layers:
            background_np, background_crop_box = apply_fisheye(background_np, strength=args.fisheye_strength, crop=args.crop_fisheye)
            object_layer_np, object_layer_crop_box = apply_fisheye(object_layer_np, strength=args.fisheye_strength, crop=args.crop_fisheye, crop_box=background_crop_box)
        else:
            height, width = background_np.shape[:2]
            background_crop_box = (0, 0, width, height)
            object_layer_np, object_layer_crop_box = apply_fisheye(object_layer_np, strength=args.fisheye_strength,crop_box=background_crop_box)
        # convert back to Image for each layer
        object_layer = Image.from_array(object_layer_np)
        background = Image.from_array(background_np)
        

        objects = adjust_bounding_boxes(objects, background_np.shape[1], background_np.shape[0], background_crop_box, strength=args.fisheye_strength)

    # composite the object layer on top of the background
    background.composite(object_layer, 0, 0)
    background.format = 'png'
    background.save(filename=fullpath)
    bbox_json["boundingBoxes"].update({f'composite.{epoch}.{i}.png': objects})
    
    try:
        if not args.skip_upload:
            res = requests.post(url=INGESTION_URL + '/api/' + upload_category + '/files',
                headers={
                    # 'x-label': label,
                    'x-api-key': API_KEY,
                    'x-metadata': json.dumps({
                        'generated_by': 'composite-image-generator',
                        'allow_overlap': str(args.allow_overlap),
                        'allow_rotate': str(args.allow_rotate),
                        'apply_motion_blur': str(args.apply_motion_blur),
                        'motion_blur_direction': str(args.motion_blur_direction),
                        'object_area': str(args.object_area),
                    }),
                    'x-synthetic-data-job-id': str(args.synthetic_data_job_id) if args.synthetic_data_job_id is not None else None,
                    'x-bounding-boxes': json.dumps(objects)
                },
                files = { 'data': (os.path.basename(fullpath), background.make_blob(), 'image/png') }
            )
            if (res.status_code != 200):
                raise Exception('Failed to upload file to Edge Impulse (status_code=' + str(res.status_code) + '): ' + res.content.decode("utf-8"))
            else:
                body = json.loads(res.content.decode("utf-8"))
                if (body['success'] != True):
                    raise Exception('Failed to upload file to Edge Impulse: ' + body['error'])
                if (body['files'][0]['success'] != True):
                    raise Exception('Failed to upload file to Edge Impulse: ' + body['files'][0]['error'])

        print(' OK')

    except Exception as e:
        print('')
        print('Failed to complete composite image generation:', e)
        print(traceback.format_exc())
        exit(1)
with open(os.path.join(args.out_directory,'bounding_boxes.labels'),'w+') as file:
        json.dump(bbox_json, file, indent = 4)
