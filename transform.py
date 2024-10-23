import os, sys, shutil
import requests
import argparse
import json
import time
import traceback
import random
from wand.image import Image
from wand.color import Color



if not os.getenv('EI_PROJECT_API_KEY'):
    print('Missing EI_PROJECT_API_KEY')
    sys.exit(1)

API_KEY = os.environ.get("EI_PROJECT_API_KEY")
INGESTION_HOST = os.environ.get("EI_INGESTION_HOST", "edgeimpulse.com")

# these are the three arguments that we get in
parser = argparse.ArgumentParser(description='Use OpenAI Dall-E to generate an image dataset for classification from your prompt')
parser.add_argument('--composite-dir', type=str, required=True, help="What folder are the source composite images found in? (there should be background and object folders)")
parser.add_argument('--labels', type=str, required=True, help="Which objects to generate images for, as a comma-separated list. Set as 'all' to generate images for all objects")
parser.add_argument('--images', type=int, required=True, help="Number of images to generate")
parser.add_argument('--objects', type=int, required=True, help="Maximum number of objects to generate")
parser.add_argument('--allow-overlap', type=int, required=True, help="Whether objects are allowed to overlap")
parser.add_argument('--allow-rotate', type=int, required=True, help="Whether to apply random rotation to objects")
parser.add_argument('--allow-motion-blur', type=int, required=True, help="Whether to apply blur to objects to simulate motion")
parser.add_argument('--motion-blur-direction', type=int, required=False, help="What direction apply blur to objects to simulate motion (-1 for random)", default=-1)

parser.add_argument('--object-area', type=str, required=True, help="x1,y1,x2,y2 coordinates of the valid area to place objects in the composite image")

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
allow_motion_blur = args.allow_motion_blur
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
# Iterate through each folder and load into a list of Image() objects if the files are images (png, bmp, jpg, jpeg)
bg_images = []
obj_images = []
for filename in os.listdir(bg_dir):
    if filename.endswith('.png') or filename.endswith('.bmp') or filename.endswith('.jpg') or filename.endswith('.jpeg'):
        bg_images.append(Image(filename=os.path.join(bg_dir, filename)))
        print('Loaded background image:', filename)
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

    if allow_motion_blur:
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
        if allow_motion_blur:
            object_image.motion_blur(sigma=blur_amount, angle=blur_direction)

        # Place the object in a random position within the defined area

        x = random.randint(object_area_left, object_area_left + object_area_width - object_width)
        y = random.randint(object_area_top, object_area_top + object_area_height - object_height)



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
            background.composite(object_image, x, y)

            # Add the object's position and size to the list of placed objects
            objects.append({'label': label, 'x': x, 'y': y, 'width': object_width, 'height': object_height})

    print(f'Created image {i+1} of {base_images_number} with {len(objects)} objects', end='', flush=True)
    fullpath = os.path.join(args.out_directory,f'composite.{epoch}.{i}.png')
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
                        'allow_motion_blur': str(args.allow_motion_blur),
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
