
### Environment Interaction 1
----------------------------------------------------------------------------
```python
# First, let's see what apps are available to work with files
print(apis.api_docs.show_app_descriptions())
```

```
[
 {
  "name": "api_docs",
  "description": "An app to search and explore API documentation."
 },
 {
  "name": "supervisor",
  "description": "An app to access supervisor's personal information, account credentials, addresses, payment cards, and manage the assigned task."
 },
 {
  "name": "amazon",
  "description": "An online shopping app to buy products and manage orders, returns, etc."
 },
 {
  "name": "phone",
  "description": "An app to find and manage contact information for friends, family members, etc., send and receive messages, and manage alarms."
 },
 {
  "name": "file_system",
  "description": "A file system app to create and manage files and folders."
 },
 {
  "name": "spotify",
  "description": "A music streaming app to stream songs and manage song, album and playlist libraries."
 },
 {
  "name": "venmo",
  "description": "A social payment app to send, receive and request money to and from others."
 },
 {
  "name": "gmail",
  "description": "An email app to draft, send, receive, and manage emails."
 },
 {
  "name": "splitwise",
  "description": "A bill splitting app to track and split expenses with people."
 },
 {
  "name": "simple_note",
  "description": "A note-taking app to create and manage notes"
 },
 {
  "name": "todoist",
  "description": "A task management app to manage todo lists and collaborate on them with others."
 }
]
```


### Environment Interaction 2
----------------------------------------------------------------------------
```python
# Let's see what APIs are available in the file_system app
print(apis.api_docs.show_api_descriptions(app_name='file_system'))
```

```
[
 {
  "name": "show_account",
  "description": "Show your account information. Unlike show_profile, this includes private information."
 },
 {
  "name": "signup",
  "description": "Sign up to create account."
 },
 {
  "name": "delete_account",
  "description": "Delete your account."
 },
 {
  "name": "update_account_name",
  "description": "Update your first or last name in the account profile."
 },
 {
  "name": "login",
  "description": "Login to your account."
 },
 {
  "name": "logout",
  "description": "Logout from your account."
 },
 {
  "name": "send_verification_code",
  "description": "Send account verification code to your email address."
 },
 {
  "name": "verify_account",
  "description": "Verify your account using the verification code sent to your email address."
 },
 {
  "name": "send_password_reset_code",
  "description": "Send password reset code to your email address."
 },
 {
  "name": "reset_password",
  "description": "Reset your password using the password reset code sent to your email address."
 },
 {
  "name": "show_profile",
  "description": "Show public profile information of a user."
 },
 {
  "name": "show_directory",
  "description": "Show a list of files and/or sub-directories, optionally recursively, in a directory."
 },
 {
  "name": "create_directory",
  "description": "Create a directory if it does not exist, optionally recursively."
 },
 {
  "name": "delete_directory",
  "description": "Delete a directory with its sub-directories and files."
 },
 {
  "name": "directory_exists",
  "description": "Check if a directory exists."
 },
 {
  "name": "show_file",
  "description": "Show a file's content and other details, if it exists."
 },
 {
  "name": "create_file",
  "description": "Create a new file with the given content."
 },
 {
  "name": "delete_file",
  "description": "Delete a file."
 },
 {
  "name": "update_file",
  "description": "Update a file's content."
 },
 {
  "name": "file_exists",
  "description": "Check if a file exists."
 },
 {
  "name": "copy_file",
  "description": "Copy a file to another location."
 },
 {
  "name": "move_file",
  "description": "Move a file to another location."
 },
 {
  "name": "copy_directory",
  "description": "Copy a directory to another location."
 },
 {
  "name": "move_directory",
  "description": "Move a directory to another location."
 },
 {
  "name": "compress_directory",
  "description": "Compress a directory."
 },
 {
  "name": "decompress_file",
  "description": "Decompress a compressed file."
 }
]
```


### Environment Interaction 3
----------------------------------------------------------------------------
```python
# Let's check the login API specification to understand authentication
print(apis.api_docs.show_api_doc(app_name='file_system', api_name='login'))
```

```
{
 "app_name": "file_system",
 "api_name": "login",
 "path": "/auth/token",
 "method": "POST",
 "description": "Login to your account.",
 "parameters": [
  {
   "name": "username",
   "type": "string",
   "required": true,
   "description": "Your account email.",
   "default": null,
   "constraints": []
  },
  {
   "name": "password",
   "type": "string",
   "required": true,
   "description": "Your account password.",
   "default": null,
   "constraints": []
  }
 ],
 "response_schemas": {
  "success": {
   "access_token": "string",
   "token_type": "string"
  },
  "failure": {
   "message": "string"
  }
 }
}
```


### Environment Interaction 4
----------------------------------------------------------------------------
```python
# Let's get my account credentials from the supervisor app
print(apis.api_docs.show_api_descriptions(app_name='supervisor'))
```

```
[
 {
  "name": "show_active_task",
  "description": "Show the currently active task assigned to you by the supervisor."
 },
 {
  "name": "complete_task",
  "description": "Mark the currently active task as complete with the given answer."
 },
 {
  "name": "show_profile",
  "description": "Show your supervisor's profile information."
 },
 {
  "name": "show_addresses",
  "description": "Show your supervisor's addresses."
 },
 {
  "name": "show_payment_cards",
  "description": "Show your supervisor's payment_cards."
 },
 {
  "name": "show_account_passwords",
  "description": "Show your supervisor's app account passwords."
 }
]
```


### Environment Interaction 5
----------------------------------------------------------------------------
```python
# Get the file system account credentials
passwords = apis.supervisor.show_account_passwords()
print(passwords)
```

```
[
 {
  "account_name": "amazon",
  "password": "dO*Pe%O"
 },
 {
  "account_name": "file_system",
  "password": "6ubYf]C"
 },
 {
  "account_name": "gmail",
  "password": "-Hgx0OM"
 },
 {
  "account_name": "phone",
  "password": "}YwMY!b"
 },
 {
  "account_name": "simple_note",
  "password": "e+of$8Z"
 },
 {
  "account_name": "splitwise",
  "password": "NY_]uMq"
 },
 {
  "account_name": "spotify",
  "password": "Qk--@(w"
 },
 {
  "account_name": "todoist",
  "password": "nPEP+e3"
 },
 {
  "account_name": "venmo",
  "password": "#XdnUVa"
 }
]
```


### Environment Interaction 6
----------------------------------------------------------------------------
```python
# Login to the file system using my email and the password
file_system_password = "6ubYf]C"
my_email = "jasonsimp@gmail.com"

login_response = apis.file_system.login(username=my_email, password=file_system_password)
print(login_response)
```

```
{
 "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJmaWxlX3N5c3RlbStqYXNvbnNpbXBAZ21haWwuY29tIiwiZXhwIjoxNjg0NDEyMDk4fQ._cqOYHfZ0nZ345YW72q2I8SdweuWDc6b4okDY-gma90",
 "token_type": "Bearer"
}
```


### Environment Interaction 7
----------------------------------------------------------------------------
```python
# Store the access token for future API calls
access_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJmaWxlX3N5c3RlbStqYXNvbnNpbXBAZ21haWwuY29tIiwiZXhwIjoxNjg0NDEyMDk4fQ._cqOYHfZ0nZ345YW72q2I8SdweuWDc6b4okDY-gma90"

# Explore the ~/photos/ directory to see the vacation spot subdirectories
photos_dir = apis.file_system.show_directory(directory="~/photos/", access_token=access_token)
print(photos_dir)
```

```
[
 "/home/",
 "/home/jason/",
 "/home/jason/backups/",
 "/home/jason/backups/laptop.zip",
 "/home/jason/backups/phone.zip",
 "/home/jason/bills/",
 "/home/jason/bills/electricity/",
 "/home/jason/bills/electricity/2022-08.pdf",
 "/home/jason/bills/electricity/2022-09.pdf",
 "/home/jason/bills/electricity/2022-10.pdf",
 "/home/jason/bills/electricity/2022-11.pdf",
 "/home/jason/bills/electricity/2022-12.pdf",
 "/home/jason/bills/electricity/2023-01.pdf",
 "/home/jason/bills/electricity/2023-02.pdf",
 "/home/jason/bills/electricity/2023-03.pdf",
 "/home/jason/bills/electricity/2023-04.pdf",
 "/home/jason/bills/electricity/2023-05.pdf",
 "/home/jason/bills/internet/",
 "/home/jason/bills/internet/2022-08.pdf",
 "/home/jason/bills/internet/2022-09.pdf",
 "/home/jason/bills/internet/2022-10.pdf",
 "/home/jason/bills/internet/2022-11.pdf",
 "/home/jason/bills/internet/2022-12.pdf",
 "/home/jason/bills/internet/2023-01.pdf",
 "/home/jason/bills/internet/2023-02.pdf",
 "/home/jason/bills/internet/2023-03.pdf",
 "/home/jason/bills/internet/2023-04.pdf",
 "/home/jason/bills/internet/2023-05.pdf",
 "/home/jason/bills/phone/",
 "/home/jason/bills/phone/2022-08.pdf",
 "/home/jason/bills/phone/2022-09.pdf",
 "/home/jason/bills/phone/2022-10.pdf",
 "/home/jason/bills/phone/2022-11.pdf",
 "/home/jason/bills/phone/2022-12.pdf",
 "/home/jason/bills/phone/2023-01.pdf",
 "/home/jason/bills/phone/2023-02.pdf",
 "/home/jason/bills/phone/2023-03.pdf",
 "/home/jason/bills/phone/2023-04.pdf",
 "/home/jason/bills/phone/2023-05.pdf",
 "/home/jason/documents/",
 "/home/jason/documents/financial/",
 "/home/jason/documents/financial/bank_statements/",
 "/home/jason/documents/financial/bank_statements/2022-09.pdf",
 "/home/jason/documents/financial/bank_statements/2022-10.pdf",
 "/home/jason/documents/financial/bank_statements/2022-11.pdf",
 "/home/jason/documents/financial/bank_statements/2022-12.pdf",
 "/home/jason/documents/financial/bank_statements/2023-01.pdf",
 "/home/jason/documents/financial/bank_statements/2023-02.pdf",
 "/home/jason/documents/financial/bank_statements/2023-03.pdf",
 "/home/jason/documents/financial/bank_statements/2023-04.pdf",
 "/home/jason/documents/financial/bank_statements/2023-05.pdf",
 "/home/jason/documents/financial/taxes/",
 "/home/jason/documents/financial/taxes/federal/",
 "/home/jason/documents/financial/taxes/federal/financial_year_2017.pdf",
 "/home/jason/documents/financial/taxes/federal/financial_year_2018.pdf",
 "/home/jason/documents/financial/taxes/federal/financial_year_2019.pdf",
 "/home/jason/documents/financial/taxes/federal/financial_year_2020.pdf",
 "/home/jason/documents/financial/taxes/federal/financial_year_2021.pdf",
 "/home/jason/documents/financial/taxes/state/",
 "/home/jason/documents/financial/taxes/state/financial_year_2017.pdf",
 "/home/jason/documents/financial/taxes/state/financial_year_2018.pdf",
 "/home/jason/documents/financial/taxes/state/financial_year_2019.pdf",
 "/home/jason/documents/financial/taxes/state/financial_year_2020.pdf",
 "/home/jason/documents/financial/taxes/state/financial_year_2021.pdf",
 "/home/jason/documents/personal/",
 "/home/jason/documents/personal/identity/",
 "/home/jason/documents/personal/identity/birth_certificate.pdf",
 "/home/jason/documents/personal/identity/driving_license.pdf",
 "/home/jason/documents/personal/identity/passport.pdf",
 "/home/jason/documents/personal/identity/ssn.pdf",
 "/home/jason/documents/work/",
 "/home/jason/documents/work/meetings/",
 "/home/jason/documents/work/meetings/2023-apr-07/",
 "/home/jason/documents/work/meetings/2023-apr-07/slides.ppt",
 "/home/jason/documents/work/meetings/2023-apr-07/video_recording.mp4",
 "/home/jason/documents/work/meetings/2023-apr-14/",
 "/home/jason/documents/work/meetings/2023-apr-14/slides.ppt",
 "/home/jason/documents/work/meetings/2023-apr-14/video_recording.mp4",
 "/home/jason/documents/work/meetings/2023-apr-21/",
 "/home/jason/documents/work/meetings/2023-apr-21/slides.ppt",
 "/home/jason/documents/work/meetings/2023-apr-21/video_recording.mp4",
 "/home/jason/documents/work/meetings/2023-apr-28/",
 "/home/jason/documents/work/meetings/2023-apr-28/slides.ppt",
 "/home/jason/documents/work/meetings/2023-apr-28/video_recording.mp4",
 "/home/jason/documents/work/meetings/2023-mar-24/",
 "/home/jason/documents/work/meetings/2023-mar-24/slides.ppt",
 "/home/jason/documents/work/meetings/2023-mar-24/video_recording.mp4",
 "/home/jason/documents/work/meetings/2023-mar-31/",
 "/home/jason/documents/work/meetings/2023-mar-31/slides.ppt",
 "/home/jason/documents/work/meetings/2023-mar-31/video_recording.mp4",
 "/home/jason/documents/work/meetings/2023-may-05/",
 "/home/jason/documents/work/meetings/2023-may-05/slides.ppt",
 "/home/jason/documents/work/meetings/2023-may-05/video_recording.mp4",
 "/home/jason/documents/work/meetings/2023-may-12/",
 "/home/jason/documents/work/meetings/2023-may-12/slides.ppt",
 "/home/jason/documents/work/meetings/2023-may-12/video_recording.mp4",
 "/home/jason/documents/work/professional_headshot.jpeg",
 "/home/jason/documents/work/resume.pdf",
 "/home/jason/downloads/",
 "/home/jason/downloads/DIY_home_decor_inspiration.rar",
 "/home/jason/downloads/ac_repair.pdf",
 "/home/jason/downloads/after_party.jpg",
 "/home/jason/downloads/anniversary_party.jpg",
 "/home/jason/downloads/art_inspiration_sketches.zip",
 "/home/jason/downloads/bird_feeder.jpg",
 "/home/jason/downloads/board_games.jpg",
 "/home/jason/downloads/business_trip.jpg",
 "/home/jason/downloads/canvas.pdf",
 "/home/jason/downloads/car_maintenance.pdf",
 "/home/jason/downloads/class_fee.jpg",
 "/home/jason/downloads/cleaning_supplies.jpg",
 "/home/jason/downloads/coding_interview_prep.docx",
 "/home/jason/downloads/coffee.jpg",
 "/home/jason/downloads/comfy_blankets.jpg",
 "/home/jason/downloads/compost.jpg",
 "/home/jason/downloads/concert_t_shirt.jpg",
 "/home/jason/downloads/conference_tickets.jpg",
 "/home/jason/downloads/cooking_masterclass_videos.zip",
 "/home/jason/downloads/corporate_gifts.pdf",
 "/home/jason/downloads/curtains.jpg",
 "/home/jason/downloads/cute_cat_gifs_collection.gif",
 "/home/jason/downloads/data_visualization_examples.ppt",
 "/home/jason/downloads/data_visualization_tutorial.mp4",
 "/home/jason/downloads/dumbbells.jpg",
 "/home/jason/downloads/escape_room_tour.jpg",
 "/home/jason/downloads/exotic_cuisine_recipe_collection.pdf",
 "/home/jason/downloads/family_dinner.jpg",
 "/home/jason/downloads/fashion_trends_lookbook.rar",
 "/home/jason/downloads/financial_investment_strategies.pdf",
 "/home/jason/downloads/financial_planning_resources.doc",
 "/home/jason/downloads/financial_planning_toolkit.xlsx",
 "/home/jason/downloads/fitness_tracker.jpg",
 "/home/jason/downloads/fitness_tracker.pdf",
 "/home/jason/downloads/futuristic_tech_concepts_presentation.ppt",
 "/home/jason/downloads/gardening_supplies.jpg",
 "/home/jason/downloads/graduation_gift.jpg",
 "/home/jason/downloads/grapes.jpg",
 "/home/jason/downloads/groceries_receipt.jpg",
 "/home/jason/downloads/grocery_delivery.jpg",
 "/home/jason/downloads/haircut.pdf",
 "/home/jason/downloads/historical_architecture_photos.rar",
 "/home/jason/downloads/holiday_decorations.jpg",
 "/home/jason/downloads/home_cleaning.pdf",
 "/home/jason/downloads/home_renovation.jpg",
 "/home/jason/downloads/hosting_supplies.jpg",
 "/home/jason/downloads/hot_chocolate.jpg",
 "/home/jason/downloads/hotel.pdf",
 "/home/jason/downloads/kitchen_utensils.jpg",
 "/home/jason/downloads/landscape_painting_tutorial.mp4",
 "/home/jason/downloads/language_learning_podcasts.mp3",
 "/home/jason/downloads/meeting_room_rental.jpg",
 "/home/jason/downloads/mindfulness_meditation_sessions.mp3",
 "/home/jason/downloads/monthly_magazine.pdf",
 "/home/jason/downloads/movie_posters.pdf",
 "/home/jason/downloads/new_bike.jpg",
 "/home/jason/downloads/new_printer.jpg",
 "/home/jason/downloads/new_watch.jpg",
 "/home/jason/downloads/office_curtains.jpg",
 "/home/jason/downloads/office_decorations.jpg",
 "/home/jason/downloads/office_lighting.pdf",
 "/home/jason/downloads/office_party.jpg",
 "/home/jason/downloads/party_signage.pdf",
 "/home/jason/downloads/payment_receipts.pdf",
 "/home/jason/downloads/phone_accessories.pdf",
 "/home/jason/downloads/photographer.pdf",
 "/home/jason/downloads/picnic_supplies.jpg",
 "/home/jason/downloads/printer_maintenance.pdf",
 "/home/jason/downloads/punch_bowl.jpg",
 "/home/jason/downloads/quick_and_easy_crafts_guide.pdf",
 "/home/jason/downloads/software_development_ebook.epub",
 "/home/jason/downloads/souvenirs.pdf",
 "/home/jason/downloads/space_discovery_documentary.mp4",
 "/home/jason/downloads/space_exploration_videos.zip",
 "/home/jason/downloads/storage_bins.pdf",
 "/home/jason/downloads/subscription_service.jpg",
 "/home/jason/downloads/tablecloth.jpg",
 "/home/jason/downloads/team_photo_session.jpg",
 "/home/jason/downloads/tool_set.jpg",
 "/home/jason/downloads/trash_removal.pdf",
 "/home/jason/downloads/travel_adventure_diary.docx",
 "/home/jason/downloads/tv_antenna.jpg",
 "/home/jason/downloads/user_test_results.pdf",
 "/home/jason/downloads/vacuum_cleaner.pdf",
 "/home/jason/downloads/virtual_art_gallery_experience.zip",
 "/home/jason/downloads/web_design_inspiration_gallery.zip",
 "/home/jason/downloads/workout_routine_demo_videos.mp4",
 "/home/jason/photos/",
 "/home/jason/photos/vacation_bali.zip",
 "/home/jason/photos/vacations/",
 "/home/jason/photos/vacations/bali/",
 "/home/jason/photos/vacations/bali/IMG_0215c47.jpeg",
 "/home/jason/photos/vacations/bali/IMG_0e9a6d2.jpg",
 "/home/jason/photos/vacations/bali/IMG_18e6223.jpg",
 "/home/jason/photos/vacations/bali/IMG_7393d77.jpg",
 "/home/jason/photos/vacations/bali/IMG_77fe15b.jpeg",
 "/home/jason/photos/vacations/bali/IMG_acb39c4.png",
 "/home/jason/photos/vacations/bali/IMG_ebb6914.png",
 "/home/jason/photos/vacations/barcelona/",
 "/home/jason/photos/vacations/barcelona/IMG_34128f4.png",
 "/home/jason/photos/vacations/barcelona/IMG_5a8a8bc.jpeg",
 "/home/jason/photos/vacations/barcelona/IMG_986800f.png",
 "/home/jason/photos/vacations/barcelona/IMG_a35f6d4.jpg",
 "/home/jason/photos/vacations/barcelona/IMG_e362a6c.jpg",
 "/home/jason/photos/vacations/barcelona/IMG_f3edf9c.jpg",
 "/home/jason/photos/vacations/kyoto/",
 "/home/jason/photos/vacations/kyoto/IMG_0751f75.jpg",
 "/home/jason/photos/vacations/kyoto/IMG_4054edb.jpeg",
 "/home/jason/photos/vacations/kyoto/IMG_74446bd.jpeg",
 "/home/jason/photos/vacations/kyoto/IMG_870b5e1.png",
 "/home/jason/photos/vacations/kyoto/IMG_c4624b3.jpg",
 "/home/jason/photos/vacations/kyoto/IMG_c688479.jpg",
 "/home/jason/photos/vacations/kyoto/IMG_cd390d2.jpg",
 "/home/jason/photos/vacations/kyoto/IMG_d2f2246.jpeg",
 "/home/jason/photos/vacations/kyoto/IMG_e76c633.jpg",
 "/home/jason/photos/vacations/kyoto/IMG_eb85e35.png",
 "/home/jason/photos/vacations/maldives/",
 "/home/jason/photos/vacations/maldives/IMG_221ee28.png",
 "/home/jason/photos/vacations/maldives/IMG_29603cc.jpg",
 "/home/jason/photos/vacations/maldives/IMG_2f5fd9a.jpeg",
 "/home/jason/photos/vacations/maldives/IMG_7bb6d57.jpg",
 "/home/jason/photos/vacations/maldives/IMG_94ba184.png",
 "/home/jason/photos/vacations/maldives/IMG_9b14937.png",
 "/home/jason/photos/vacations/maldives/IMG_ba32252.png",
 "/home/jason/photos/vacations/maldives/IMG_c12bff5.png",
 "/home/jason/photos/vacations/maldives/IMG_f1959ce.jpg",
 "/home/jason/photos/vacations/maldives/IMG_ffea32a.jpg",
 "/home/jason/trash/",
 "/home/jason/trash/art_sketches_and_doodles.rar",
 "/home/jason/trash/fashion_design_inspiration_gallery.zip",
 "/home/jason/trash/health_and_wellness_podcasts.mp3",
 "/home/jason/trash/historical_fiction_novel.epub",
 "/home/jason/trash/historical_timeline_presentation.ppt",
 "/home/jason/trash/mindfulness_and_selfcare_guide.docx",
 "/home/jason/trash/mystery_novel_ebook.epub",
 "/home/jason/trash/space_exploration_documentary.mp4",
 "/home/jason/trash/space_exploration_encyclopedia.epub",
 "/home/jason/trash/workout_progress_tracker.doc"
]
```


### Environment Interaction 8
----------------------------------------------------------------------------
```python
# Check the compress_directory API specification
print(apis.api_docs.show_api_doc(app_name='file_system', api_name='compress_directory'))
```

```
{
 "app_name": "file_system",
 "api_name": "compress_directory",
 "path": "/directory/compress",
 "method": "POST",
 "description": "Compress a directory.",
 "parameters": [
  {
   "name": "directory_path",
   "type": "string",
   "required": true,
   "description": "Path of the directory to compress. Path can be absolute, starting with '/', or relative to the user's home directory, starting with '~/'.",
   "default": null,
   "constraints": []
  },
  {
   "name": "access_token",
   "type": "string",
   "required": true,
   "description": "Access token obtained from file_system app login.",
   "default": null,
   "constraints": []
  },
  {
   "name": "compressed_file_path",
   "type": "string",
   "required": false,
   "description": "Path of the compressed file. Path can be absolute, starting with '/', or relative to the user's home directory, starting with '~/'. If passed, must have an extension: zip or tar. If not passed, it'll be stored as a zip file with the base name of directory_path in directory_path's parent directory.",
   "default": null,
   "constraints": []
  },
  {
   "name": "delete_directory",
   "type": "boolean",
   "required": false,
   "description": "Whether to delete the directory after compression.",
   "default": false,
   "constraints": []
  },
  {
   "name": "overwrite",
   "type": "boolean",
   "required": false,
   "description": "Whether to overwrite the compressed file if it already exists.",
   "default": false,
   "constraints": []
  }
 ],
 "response_schemas": {
  "success": {
   "message": "string",
   "compressed_file_path": "string"
  },
  "failure": {
   "message": "string"
  }
 }
}
```


### Environment Interaction 9
----------------------------------------------------------------------------
```python
# First, let's compress the 'bali' directory
bali_compress = apis.file_system.compress_directory(
    directory_path="~/photos/vacations/bali/",
    compressed_file_path="~/photos/vacations/bali.tar",
    delete_directory=True,
    overwrite=True,
    access_token=access_token
)
print("Bali compression result:")
print(bali_compress)
```

```
Bali compression result:
{
 "message": "Directory compressed.",
 "compressed_file_path": "/home/jason/photos/vacations/bali.tar"
}
```


### Environment Interaction 10
----------------------------------------------------------------------------
```python
# Compress the 'barcelona' directory
barcelona_compress = apis.file_system.compress_directory(
    directory_path="~/photos/vacations/barcelona/",
    compressed_file_path="~/photos/vacations/barcelona.tar",
    delete_directory=True,
    overwrite=True,
    access_token=access_token
)
print("Barcelona compression result:")
print(barcelona_compress)
```

```
Barcelona compression result:
{
 "message": "Directory compressed.",
 "compressed_file_path": "/home/jason/photos/vacations/barcelona.tar"
}
```


### Environment Interaction 11
----------------------------------------------------------------------------
```python
# Compress the 'kyoto' directory
kyoto_compress = apis.file_system.compress_directory(
    directory_path="~/photos/vacations/kyoto/",
    compressed_file_path="~/photos/vacations/kyoto.tar",
    delete_directory=True,
    overwrite=True,
    access_token=access_token
)
print("Kyoto compression result:")
print(kyoto_compress)
```

```
Kyoto compression result:
{
 "message": "Directory compressed.",
 "compressed_file_path": "/home/jason/photos/vacations/kyoto.tar"
}
```


### Environment Interaction 12
----------------------------------------------------------------------------
```python
# Compress the 'maldives' directory
maldives_compress = apis.file_system.compress_directory(
    directory_path="~/photos/vacations/maldives/",
    compressed_file_path="~/photos/vacations/maldives.tar",
    delete_directory=True,
    overwrite=True,
    access_token=access_token
)
print("Maldives compression result:")
print(maldives_compress)
```

```
Maldives compression result:
{
 "message": "Directory compressed.",
 "compressed_file_path": "/home/jason/photos/vacations/maldives.tar"
}
```


### Environment Interaction 13
----------------------------------------------------------------------------
```python
# Verify the final state of the vacations directory
final_state = apis.file_system.show_directory(directory="~/photos/vacations/", access_token=access_token)
print("Final state of ~/photos/vacations/ directory:")
for item in final_state:
    if "/home/jason/photos/vacations/" in item:
        print(item)
```

```
Final state of ~/photos/vacations/ directory:
/home/jason/photos/vacations/
/home/jason/photos/vacations/bali.tar
/home/jason/photos/vacations/barcelona.tar
/home/jason/photos/vacations/kyoto.tar
/home/jason/photos/vacations/maldives.tar
```


### Environment Interaction 14
----------------------------------------------------------------------------
```python
# Task completion summary
print("Task Summary:")
print("✓ Successfully compressed 4 vacation spot directories:")
print("  - bali → ~/photos/vacations/bali.tar")
print("  - barcelona → ~/photos/vacations/barcelona.tar") 
print("  - kyoto → ~/photos/vacations/kyoto.tar")
print("  - maldives → ~/photos/vacations/maldives.tar")
print("✓ All original vacation spot subdirectories have been deleted")
print("✓ All compressed files are saved in the ~/photos/vacations/ directory")

# Complete the task
apis.supervisor.complete_task()
```

```
Task Summary:
✓ Successfully compressed 4 vacation spot directories:
  - bali → ~/photos/vacations/bali.tar
  - barcelona → ~/photos/vacations/barcelona.tar
  - kyoto → ~/photos/vacations/kyoto.tar
  - maldives → ~/photos/vacations/maldives.tar
✓ All original vacation spot subdirectories have been deleted
✓ All compressed files are saved in the ~/photos/vacations/ directory
```

