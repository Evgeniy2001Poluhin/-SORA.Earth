export interface RussianRegion {
  code: string;
  name: string;
  capital: string;
  district: "ЦФО" | "СЗФО" | "ЮФО" | "СКФО" | "ПФО" | "УФО" | "СФО" | "ДФО";
  lat: number;
  lon: number;
  population: number;
  esgScore?: number;
}

/** Per-pillar ESG breakdown as returned by GET /api/v1/map/russia. */
export interface RegionEsgBreakdown {
  score: number;
  e_score: number;
  s_score: number;
  g_score: number;
}

/**
 * A static region merged with the live values from /api/v1/map/russia.
 * RussiaMapModal builds these; RussiaMap renders the extra fields when present.
 */
export interface EnrichedRussianRegion extends RussianRegion {
  esgBreakdown?: RegionEsgBreakdown;
  confidence?: number;
  sourcesUsed?: string[];
  updatedAt?: string | null;
}

export const RUSSIA_REGIONS: RussianRegion[] = [
  { code: "RU-MOW", name: "Москва", capital: "Москва", district: "ЦФО", lat: 55.7558, lon: 37.6176, population: 13149803 },
  { code: "RU-MOS", name: "Московская область", capital: "Красногорск", district: "ЦФО", lat: 55.8316, lon: 37.3295, population: 8524665 },
  { code: "RU-BEL", name: "Белгородская область", capital: "Белгород", district: "ЦФО", lat: 50.5952, lon: 36.5873, population: 1531917 },
  { code: "RU-BRY", name: "Брянская область", capital: "Брянск", district: "ЦФО", lat: 53.2434, lon: 34.3644, population: 1168771 },
  { code: "RU-VLA", name: "Владимирская область", capital: "Владимир", district: "ЦФО", lat: 56.1290, lon: 40.4070, population: 1323659 },
  { code: "RU-VOR", name: "Воронежская область", capital: "Воронеж", district: "ЦФО", lat: 51.6605, lon: 39.2005, population: 2287678 },
  { code: "RU-IVA", name: "Ивановская область", capital: "Иваново", district: "ЦФО", lat: 57.0004, lon: 40.9739, population: 987032 },
  { code: "RU-KLU", name: "Калужская область", capital: "Калуга", district: "ЦФО", lat: 54.5293, lon: 36.2754, population: 1000980 },
  { code: "RU-KOS", name: "Костромская область", capital: "Кострома", district: "ЦФО", lat: 57.7665, lon: 40.9265, population: 628103 },
  { code: "RU-KRS", name: "Курская область", capital: "Курск", district: "ЦФО", lat: 51.7304, lon: 36.1926, population: 1077741 },
  { code: "RU-LIP", name: "Липецкая область", capital: "Липецк", district: "ЦФО", lat: 52.6088, lon: 39.5992, population: 1115276 },
  { code: "RU-ORL", name: "Орловская область", capital: "Орёл", district: "ЦФО", lat: 52.9691, lon: 36.0689, population: 717893 },
  { code: "RU-RYA", name: "Рязанская область", capital: "Рязань", district: "ЦФО", lat: 54.6291, lon: 39.7413, population: 1085717 },
  { code: "RU-SMO", name: "Смоленская область", capital: "Смоленск", district: "ЦФО", lat: 54.7828, lon: 32.0453, population: 894216 },
  { code: "RU-TAM", name: "Тамбовская область", capital: "Тамбов", district: "ЦФО", lat: 52.7213, lon: 41.4523, population: 976038 },
  { code: "RU-TVE", name: "Тверская область", capital: "Тверь", district: "ЦФО", lat: 56.8587, lon: 35.9176, population: 1226798 },
  { code: "RU-TUL", name: "Тульская область", capital: "Тула", district: "ЦФО", lat: 54.1961, lon: 37.6182, population: 1450099 },
  { code: "RU-YAR", name: "Ярославская область", capital: "Ярославль", district: "ЦФО", lat: 57.6261, lon: 39.8845, population: 1209810 },
  { code: "RU-SPE", name: "Санкт-Петербург", capital: "Санкт-Петербург", district: "СЗФО", lat: 59.9343, lon: 30.3351, population: 5600044 },
  { code: "RU-LEN", name: "Ленинградская область", capital: "Гатчина", district: "СЗФО", lat: 59.5624, lon: 30.1276, population: 2020373 },
  { code: "RU-KR", name: "Республика Карелия", capital: "Петрозаводск", district: "СЗФО", lat: 61.7849, lon: 34.3469, population: 527879 },
  { code: "RU-KO", name: "Республика Коми", capital: "Сыктывкар", district: "СЗФО", lat: 61.6688, lon: 50.8359, population: 737853 },
  { code: "RU-ARK", name: "Архангельская область", capital: "Архангельск", district: "СЗФО", lat: 64.5401, lon: 40.5433, population: 984200 },
  { code: "RU-NEN", name: "Ненецкий АО", capital: "Нарьян-Мар", district: "СЗФО", lat: 67.6380, lon: 53.0063, population: 40970 },
  { code: "RU-VLG", name: "Вологодская область", capital: "Вологда", district: "СЗФО", lat: 59.2239, lon: 39.8840, population: 1111080 },
  { code: "RU-KGD", name: "Калининградская область", capital: "Калининград", district: "СЗФО", lat: 54.7104, lon: 20.4522, population: 1029966 },
  { code: "RU-MUR", name: "Мурманская область", capital: "Мурманск", district: "СЗФО", lat: 68.9585, lon: 33.0827, population: 657001 },
  { code: "RU-NGR", name: "Новгородская область", capital: "Великий Новгород", district: "СЗФО", lat: 58.5213, lon: 31.2755, population: 582093 },
  { code: "RU-PSK", name: "Псковская область", capital: "Псков", district: "СЗФО", lat: 57.8194, lon: 28.3317, population: 611212 },
  { code: "RU-AD", name: "Республика Адыгея", capital: "Майкоп", district: "ЮФО", lat: 44.6098, lon: 40.1006, population: 496934 },
  { code: "RU-KL", name: "Республика Калмыкия", capital: "Элиста", district: "ЮФО", lat: 46.3083, lon: 44.2558, population: 267133 },
  { code: "RU-KDA", name: "Краснодарский край", capital: "Краснодар", district: "ЮФО", lat: 45.0355, lon: 38.9753, population: 5838273 },
  { code: "RU-AST", name: "Астраханская область", capital: "Астрахань", district: "ЮФО", lat: 46.3497, lon: 48.0408, population: 976126 },
  { code: "RU-VGG", name: "Волгоградская область", capital: "Волгоград", district: "ЮФО", lat: 48.7080, lon: 44.5133, population: 2474556 },
  { code: "RU-ROS", name: "Ростовская область", capital: "Ростов-на-Дону", district: "ЮФО", lat: 47.2357, lon: 39.7015, population: 4153763 },
  { code: "RU-CR", name: "Республика Крым", capital: "Симферополь", district: "ЮФО", lat: 44.9521, lon: 34.1024, population: 1901868 },
  { code: "RU-SEV", name: "Севастополь", capital: "Севастополь", district: "ЮФО", lat: 44.6166, lon: 33.5254, population: 561168 },
  { code: "RU-DA", name: "Республика Дагестан", capital: "Махачкала", district: "СКФО", lat: 42.9849, lon: 47.5047, population: 3182054 },
  { code: "RU-IN", name: "Республика Ингушетия", capital: "Магас", district: "СКФО", lat: 43.1688, lon: 44.8132, population: 524116 },
  { code: "RU-KB", name: "Кабардино-Балкария", capital: "Нальчик", district: "СКФО", lat: 43.4845, lon: 43.6070, population: 904200 },
  { code: "RU-KC", name: "Карачаево-Черкесия", capital: "Черкесск", district: "СКФО", lat: 44.2235, lon: 42.0457, population: 467617 },
  { code: "RU-SE", name: "Северная Осетия", capital: "Владикавказ", district: "СКФО", lat: 43.0247, lon: 44.6811, population: 687357 },
  { code: "RU-CE", name: "Чеченская Республика", capital: "Грозный", district: "СКФО", lat: 43.3181, lon: 45.6949, population: 1510824 },
  { code: "RU-STA", name: "Ставропольский край", capital: "Ставрополь", district: "СКФО", lat: 45.0445, lon: 41.9690, population: 2907592 },
  { code: "RU-BA", name: "Республика Башкортостан", capital: "Уфа", district: "ПФО", lat: 54.7388, lon: 55.9721, population: 4013786 },
  { code: "RU-ME", name: "Республика Марий Эл", capital: "Йошкар-Ола", district: "ПФО", lat: 56.6388, lon: 47.8908, population: 675329 },
  { code: "RU-MO", name: "Республика Мордовия", capital: "Саранск", district: "ПФО", lat: 54.1838, lon: 45.1749, population: 778942 },
  { code: "RU-TA", name: "Республика Татарстан", capital: "Казань", district: "ПФО", lat: 55.7961, lon: 49.1064, population: 4004809 },
  { code: "RU-UD", name: "Удмуртская Республика", capital: "Ижевск", district: "ПФО", lat: 56.8526, lon: 53.2045, population: 1491155 },
  { code: "RU-CU", name: "Чувашская Республика", capital: "Чебоксары", district: "ПФО", lat: 56.1322, lon: 47.2519, population: 1203169 },
  { code: "RU-KIR", name: "Кировская область", capital: "Киров", district: "ПФО", lat: 58.6035, lon: 49.6679, population: 1234433 },
  { code: "RU-NIZ", name: "Нижегородская область", capital: "Нижний Новгород", district: "ПФО", lat: 56.2965, lon: 43.9361, population: 3202946 },
  { code: "RU-ORE", name: "Оренбургская область", capital: "Оренбург", district: "ПФО", lat: 51.7727, lon: 55.0988, population: 1862767 },
  { code: "RU-PNZ", name: "Пензенская область", capital: "Пенза", district: "ПФО", lat: 53.2007, lon: 45.0046, population: 1266055 },
  { code: "RU-PER", name: "Пермский край", capital: "Пермь", district: "ПФО", lat: 58.0105, lon: 56.2502, population: 2532405 },
  { code: "RU-SAM", name: "Самарская область", capital: "Самара", district: "ПФО", lat: 53.1959, lon: 50.1002, population: 3131721 },
  { code: "RU-SAR", name: "Саратовская область", capital: "Саратов", district: "ПФО", lat: 51.5924, lon: 45.9608, population: 2395111 },
  { code: "RU-ULY", name: "Ульяновская область", capital: "Ульяновск", district: "ПФО", lat: 54.3142, lon: 48.4032, population: 1197026 },
  { code: "RU-KGN", name: "Курганская область", capital: "Курган", district: "УФО", lat: 55.4410, lon: 65.3411, population: 798652 },
  { code: "RU-SVE", name: "Свердловская область", capital: "Екатеринбург", district: "УФО", lat: 56.8389, lon: 60.6057, population: 4268998 },
  { code: "RU-TYU", name: "Тюменская область", capital: "Тюмень", district: "УФО", lat: 57.1530, lon: 65.5343, population: 1591839 },
  { code: "RU-KHM", name: "ХМАО-Югра", capital: "Ханты-Мансийск", district: "УФО", lat: 61.0042, lon: 69.0019, population: 1711480 },
  { code: "RU-YAN", name: "Ямало-Ненецкий АО", capital: "Салехард", district: "УФО", lat: 66.5299, lon: 66.6138, population: 510490 },
  { code: "RU-CHE", name: "Челябинская область", capital: "Челябинск", district: "УФО", lat: 55.1644, lon: 61.4368, population: 3418620 },
  { code: "RU-AL", name: "Республика Алтай", capital: "Горно-Алтайск", district: "СФО", lat: 51.9587, lon: 85.9600, population: 210923 },
  { code: "RU-TY", name: "Республика Тыва", capital: "Кызыл", district: "СФО", lat: 51.7191, lon: 94.4378, population: 336651 },
  { code: "RU-KK", name: "Республика Хакасия", capital: "Абакан", district: "СФО", lat: 53.7196, lon: 91.4292, population: 528337 },
  { code: "RU-ALT", name: "Алтайский край", capital: "Барнаул", district: "СФО", lat: 53.3481, lon: 83.7798, population: 2268179 },
  { code: "RU-KYA", name: "Красноярский край", capital: "Красноярск", district: "СФО", lat: 56.0184, lon: 92.8672, population: 2849174 },
  { code: "RU-IRK", name: "Иркутская область", capital: "Иркутск", district: "СФО", lat: 52.2869, lon: 104.3050, population: 2375021 },
  { code: "RU-KEM", name: "Кемеровская область", capital: "Кемерово", district: "СФО", lat: 55.3547, lon: 86.0875, population: 2604272 },
  { code: "RU-NVS", name: "Новосибирская область", capital: "Новосибирск", district: "СФО", lat: 55.0084, lon: 82.9357, population: 2797176 },
  { code: "RU-OMS", name: "Омская область", capital: "Омск", district: "СФО", lat: 54.9885, lon: 73.3242, population: 1858798 },
  { code: "RU-TOM", name: "Томская область", capital: "Томск", district: "СФО", lat: 56.4846, lon: 84.9476, population: 1066226 },
  { code: "RU-BU", name: "Республика Бурятия", capital: "Улан-Удэ", district: "ДФО", lat: 51.8334, lon: 107.5843, population: 975890 },
  { code: "RU-SA", name: "Республика Саха", capital: "Якутск", district: "ДФО", lat: 62.0283, lon: 129.7319, population: 997565 },
  { code: "RU-ZAB", name: "Забайкальский край", capital: "Чита", district: "ДФО", lat: 52.0317, lon: 113.5007, population: 1043471 },
  { code: "RU-KAM", name: "Камчатский край", capital: "Петропавловск-Камчатский", district: "ДФО", lat: 53.0167, lon: 158.6500, population: 288956 },
  { code: "RU-PRI", name: "Приморский край", capital: "Владивосток", district: "ДФО", lat: 43.1155, lon: 131.8855, population: 1863011 },
  { code: "RU-KHA", name: "Хабаровский край", capital: "Хабаровск", district: "ДФО", lat: 48.4827, lon: 135.0838, population: 1283660 },
  { code: "RU-AMU", name: "Амурская область", capital: "Благовещенск", district: "ДФО", lat: 50.2907, lon: 127.5272, population: 759649 },
  { code: "RU-MAG", name: "Магаданская область", capital: "Магадан", district: "ДФО", lat: 59.5638, lon: 150.8035, population: 134936 },
  { code: "RU-SAK", name: "Сахалинская область", capital: "Южно-Сахалинск", district: "ДФО", lat: 46.9588, lon: 142.7386, population: 463885 },
  { code: "RU-YEV", name: "Еврейская АО", capital: "Биробиджан", district: "ДФО", lat: 48.7948, lon: 132.9246, population: 150453 },
  { code: "RU-CHU", name: "Чукотский АО", capital: "Анадырь", district: "ДФО", lat: 64.7337, lon: 177.4988, population: 47450 },
];

export const FD_COLORS: Record<RussianRegion["district"], string> = {
  "ЦФО":  "#4C9AFF",
  "СЗФО": "#00B8D9",
  "ЮФО":  "#36B37E",
  "СКФО": "#FFAB00",
  "ПФО":  "#FF8B00",
  "УФО":  "#FF5630",
  "СФО":  "#8777D9",
  "ДФО":  "#E774BB",
};