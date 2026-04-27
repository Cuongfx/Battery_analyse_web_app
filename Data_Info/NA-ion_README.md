# README

This dataset consists of 31 commercial sodium-ion batteries in 18650 cylindrical format whose positive and negative electrodes are not disclosed. The electrolyte remains unknown. The nominal capacity of all batteries is 1.0 Ah. There are 12 different charge/discharge protocols in this dataset at 25 degrees Celsius which you can find in the following part: [Charge/discharge protocols](#Charge/discharge protocols).

**Note for nominal capacity:** Due to the different cycling conditions from the original manufacturer for all sodium-ion batteries, we set the nominal capacity at 1.0 Ah after cycling the batteries by our cycling conditions.



If you use this dataset, please cite the original papers:

- **BatteryLife: A Comprehensive Dataset and Benchmark for Battery Life Prediction** 



| Battery Number | Format | Chemical System | Operation Temperature | Protocol | Data Source | Battery Type | Year |
| -------------- | ------ | --------------- | --------------------- | -------- | ----------- | ------------ | ---- |
| 31             | 1      | 1               | 2                     | 12       | Lab test    | Na-ion       | 2025 |



The original datasets are available at:

- [Battery-Life/BatteryLife_Raw · Datasets at Hugging Face](https://huggingface.co/datasets/Battery-Life/BatteryLife_Raw)
  - The data download tutorial is available at [BatteryLife/assets/Data_download.md at main · Ruifeng-Tan/BatteryLife](https://github.com/Ruifeng-Tan/BatteryLife/blob/main/assets/Data_download.md).
- [BatteryLife_Raw](https://zenodo.org/records/14904364)



## Charge/discharge protocols

Here is the detailed cycling test information for the Na-ion dataset:

| File_name                                     | Current | Temperature(℃) | Nominal capacity(Ah) |
| --------------------------------------------- | ------- | -------------- | -------------------- |
| 2750-30_20250115171823_DefaultGroup_45_2.xlsx | 2.75C   | 30             | 1                    |
| 2850-30_20250117105706_DefaultGroup_45_2.xlsx | 2.9C    | 30             | 1                    |
| 4000-30_20250115110135_DefaultGroup_45_7.xlsx | 3.9C    | 30             | 1                    |
| 4000-30_20250115110206_DefaultGroup_45_1.xlsx | 3.9C    | 30             | 1                    |
| 4500-30_20250114232539_DefaultGroup_45_8.xlsx | 4.5C    | 30             | 1                    |
| 5000-25_20250115110326_DefaultGroup_38_1.xlsx | 5C      | 25             | 1                    |
| 5000-25_20250115110326_DefaultGroup_38_2.xlsx | 5C      | 25             | 1                    |
| 5000-25_20250115110326_DefaultGroup_38_5.xlsx | 5C      | 25             | 1                    |
| 5000-25_20250115110326_DefaultGroup_38_7.xlsx | 5C      | 25             | 1                    |
| 5000-25_20250115110326_DefaultGroup_38_8.xlsx | 5C      | 25             | 1                    |
| 270040-1-1-64.xlsx                            | 2C      | 25             | 1                    |
| 270040-1-2-63.xlsx                            | 5C      | 25             | 1                    |
| 270040-1-3-62.xlsx                            | 4.2C    | 25             | 1                    |
| 270040-1-4-61.xlsx                            | 5.8C    | 25             | 1                    |
| 270040-1-5-60.xlsx                            | 5C      | 25             | 1                    |
| 270040-1-6-59.xlsx                            | 5.7C    | 25             | 1                    |
| 270040-1-7-58.xlsx                            | 5.3C    | 25             | 1                    |
| 270040-1-8-57.xlsx                            | 5.8C    | 25             | 1                    |
| 270040-2-1-12.xlsx                            | 3C      | 25             | 1                    |
| 270040-2-2-12.xlsx                            | 3C      | 25             | 1                    |
| 270040-2-3-12.xlsx                            | 3C      | 25             | 1                    |
| 270040-2-4-12.xlsx                            | 3C      | 25             | 1                    |
| 270040-2-5-12.xlsx                            | 3C      | 25             | 1                    |
| 270040-2-6-12.xlsx                            | 3C      | 25             | 1                    |
| 270040-2-7-12.xlsx                            | 3C      | 25             | 1                    |
| 270040-2-8-12.xlsx                            | 3C      | 25             | 1                    |
| 270040-3-1-56.xlsx                            | 4.7C    | 25             | 1                    |
| 270040-3-2-55.xlsx                            | 5.1C    | 25             | 1                    |
| 270040-3-3-54.xlsx                            | 5.3C    | 25             | 1                    |
| 270040-3-4-53.xlsx                            | 5.3C    | 25             | 1                    |
| 270040-3-5-52.xlsx                            | 6C      | 25             | 1                    |
| 270040-3-6-51.xlsx                            | 6C      | 25             | 1                    |
| 270040-3-7-50.xlsx                            | 4.2C    | 25             | 1                    |
| 270040-3-8-49.xlsx                            | 6C      | 25             | 1                    |
| 270040-4-1-48.xlsx                            | 6C      | 25             | 1                    |
| 270040-4-2-47.xlsx                            | 4.8C    | 25             | 1                    |
| 270040-4-3-46.xlsx                            | 5.8C    | 25             | 1                    |
| 270040-4-4-45.xlsx                            | 5.7C    | 25             | 1                    |
| 270040-4-5-44.xlsx                            | 6C      | 25             | 1                    |
| 270040-4-6-43.xlsx                            | 4.5C    | 25             | 1                    |
| 270040-4-7-42.xlsx                            | 6C      | 25             | 1                    |
| 270040-4-8-41.xlsx                            | 2C      | 25             | 1                    |
| 270040-5-1-39.xlsx                            | 5.1C    | 25             | 1                    |
| 270040-5-2-38.xlsx                            | 5.1C    | 25             | 1                    |
| 270040-5-3-37.xlsx                            | 5.1C    | 25             | 1                    |
| 270040-5-4-36.xlsx                            | 2.5C    | 25             | 1                    |
| 270040-5-5-35.xlsx                            | 5.7C    | 25             | 1                    |
| 270040-5-6-34.xlsx                            | 5.1C    | 25             | 1                    |
| 270040-5-7-33.xlsx                            | 5C      | 25             | 1                    |
| 270040-5-8-32.xlsx                            | 4.5C    | 25             | 1                    |
| 270040-6-1-31.xlsx                            | 6C      | 25             | 1                    |
| 270040-6-2-30.xlsx                            | 4C      | 25             | 1                    |
| 270040-6-3-29.xlsx                            | 6C      | 25             | 1                    |
| 270040-6-4-28.xlsx                            | 6C      | 25             | 1                    |
| 270040-6-5-27.xlsx                            | 2C      | 25             | 1                    |
| 270040-6-6-26.xlsx                            | 5.7C    | 25             | 1                    |
| 270040-6-7-25.xlsx                            | 6C      | 25             | 1                    |
| 270040-6-8-24.xlsx                            | 5.7C    | 25             | 1                    |
| 270040-7-1-23.xlsx                            | 5.8C    | 25             | 1                    |
| 270040-7-2-22.xlsx                            | 4C      | 25             | 1                    |
| 270040-7-3-21.xlsx                            | 6C      | 25             | 1                    |
| 270040-8-1-20.xlsx                            | 6C      | 25             | 1                    |
| 270040-8-2-19.xlsx                            | 6C      | 25             | 1                    |
| 270040-8-3-18.xlsx                            | 2C      | 25             | 1                    |
| 270040-8-4-17.xlsx                            | 6C      | 25             | 1                    |
| 270040-8-5-16.xlsx                            | 3C      | 25             | 1                    |
| 270040-8-6-15.xlsx                            | 6C      | 25             | 1                    |
| 270040-8-7-14.xlsx                            | 6C      | 25             | 1                    |
| 270040-8-8-13.xlsx                            | 6C      | 25             | 1                    |
